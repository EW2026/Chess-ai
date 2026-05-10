#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command};
#[cfg(debug_assertions)]
use std::process::Stdio;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::{thread, time::Duration};
#[cfg(not(debug_assertions))]
use tauri::Manager;

// Assign the child process to a Windows Job Object with KILL_ON_JOB_CLOSE.
// When this process exits for any reason (normal, crash, or kill), the OS
// automatically terminates every process in the job — including run_server.exe
// and all its PyInstaller multiprocessing workers.
#[cfg(windows)]
unsafe fn assign_to_job_object(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
    if job == 0 {
        return;
    }

    let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

    SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        &mut info as *mut _ as *mut _,
        std::mem::size_of_val(&info) as u32,
    );

    let ok = AssignProcessToJobObject(job, child.as_raw_handle() as HANDLE);
    if ok == 0 {
        // Assignment failed — job object won't kill the backend on exit.
        // kill_tree in the ExitRequested handler is the fallback.
        eprintln!("WARNING: AssignProcessToJobObject failed (error {})",
            windows_sys::Win32::Foundation::GetLastError());
    }

    // Intentionally leak the job handle — the OS kills all job members when
    // our process exits and the handle is implicitly closed.
}

fn kill_tree(child: &mut Child) {
    // child.kill() only terminates the root process. PyInstaller's multiprocessing
    // pool spawns worker processes as children of the backend, and those workers
    // stay alive if we only kill the parent.
    let pid = child.id().to_string();
    #[cfg(windows)]
    {
        // taskkill /F /T kills the entire process tree rooted at the given PID.
        let _ = Command::new("taskkill")
            .args(["/F", "/T", "/PID", &pid])
            .output();
    }
    #[cfg(not(windows))]
    {
        // pkill -P <pid> sends SIGTERM to every child of the given PID.
        let _ = Command::new("pkill").args(["-P", &pid]).output();
    }
    let _ = child.kill();
}

fn main() {
    // Arc (Atomic Reference Counted pointer) lets multiple owners share the same data.
    // Mutex ensures only one owner can access the Child handle at a time.
    // We need both because the handle is owned by the setup closure AND the exit
    // handler, which run in different contexts and potentially different threads.
    let backend_child: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));

    // Three clones: setup (spawns backend), close (CloseRequested), exit (ExitRequested/Exit).
    // All three point to the same Mutex; the first one to run sets *guard = None so the
    // others become no-ops and kill_tree is called exactly once.
    let backend_child_setup = backend_child.clone();
    let backend_child_close = backend_child.clone();
    let backend_child_exit  = backend_child.clone();

    let app = tauri::Builder::default()
        .setup(move |app| {
            // In dev mode (cargo tauri dev), use the PyInstaller output in the source tree.
            // In a production build (cargo tauri build / installed MSI), the backend EXE is
            // bundled as a Tauri resource and extracted next to the app at install time.
            // Using the dev path in production would launch whatever .exe happened to be in
            // the developer's source folder — the old version — instead of the bundled one.
            #[cfg(debug_assertions)]
            let exe_path: PathBuf = {
                let exe_name = if cfg!(windows) { "run_server.exe" } else { "run_server" };
                [env!("CARGO_MANIFEST_DIR"), "..", "..", "backend", "dist", "run_server", exe_name]
                    .iter()
                    .collect()
            };

            // In production, resources are placed relative to the install directory,
            // preserving the path structure from src-tauri/. The spec bundles the
            // entire run_server/ PyInstaller output directory as a resource glob
            // ("resources/run_server/**/*"), so the exe lives at that subdirectory.
            #[cfg(not(debug_assertions))]
            let exe_path: PathBuf = {
                let exe_name = if cfg!(windows) { "run_server.exe" } else { "run_server" };
                app.path()
                    .resource_dir()
                    .expect("could not resolve resource dir")
                    .join("resources")
                    .join("run_server")
                    .join(exe_name)
            };

            // Suppress the "unused variable" warning in debug builds where only the
            // cfg(debug_assertions) path above is compiled and `app` is not referenced.
            #[cfg(debug_assertions)]
            let _ = &app;

            println!("Backend path: {:?}", exe_path);
            println!("Exists? {}", exe_path.exists());

            if !exe_path.exists() {
                panic!("Backend EXE not found at {:?}", exe_path);
            }

            // Spawn the Django/Waitress backend as a child process.
            // CREATE_NO_WINDOW (0x08000000) prevents a console window from appearing —
            // the backend is an implementation detail the user should never see.
            // We store the Child handle so we can kill the process tree on exit.
            let mut cmd = Command::new(&exe_path);
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW — prevents a console window appearing
            }
            cmd.arg("--parent-pid")
                .arg(std::process::id().to_string());
            // In dev mode pipe backend stdio so we can forward it through Rust's own stdout,
            // which is definitely connected to the cargo tauri dev terminal.
            // Stdio::inherit() doesn't work here because the PyInstaller EXE has no console
            // handle when launched with CREATE_NO_WINDOW, so writes silently vanish.
            #[cfg(debug_assertions)]
            cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
            #[cfg(debug_assertions)]
            let mut child = cmd.spawn().expect("Failed to start backend EXE");
            #[cfg(not(debug_assertions))]
            let child = cmd.spawn().expect("Failed to start backend EXE");
            #[cfg(debug_assertions)]
            {
                use std::io::{BufRead, BufReader};
                if let Some(out) = child.stdout.take() {
                    thread::spawn(move || BufReader::new(out).lines().flatten().for_each(|l| println!("{l}")));
                }
                if let Some(err) = child.stderr.take() {
                    thread::spawn(move || BufReader::new(err).lines().flatten().for_each(|l| eprintln!("{l}")));
                }
            }

            // Attach the backend to a job object so the OS kills it (and all its
            // worker children) automatically when this Tauri process exits.
            #[cfg(windows)]
            unsafe { assign_to_job_object(&child); }

            *backend_child_setup.lock().unwrap() = Some(child);

            println!("Backend launched");

            // Wait for the backend to finish starting up (migrations, pool warmup, etc.)
            // before the webview loads and starts making API requests.
            thread::sleep(Duration::from_millis(2000));

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![])
        // CloseRequested fires the instant the user clicks X — earlier than ExitRequested,
        // which only arrives after the event loop has fully processed the close.
        // Killing the backend here prevents any window between close and exit.
        .on_window_event(move |_window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Ok(mut guard) = backend_child_close.lock() {
                    if let Some(ref mut child) = *guard {
                        kill_tree(child);
                    }
                    *guard = None;
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error building tauri application");

    // ExitRequested fires when all windows have closed and the runtime is about to exit —
    // still early enough that spawning taskkill works reliably. Exit fires after the
    // event loop itself has stopped, which is too late on some Windows configurations.
    // We handle both so the kill runs even if ExitRequested is somehow skipped.
    app.run(move |_app_handle, event| {
        match event {
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                if let Ok(mut guard) = backend_child_exit.lock() {
                    if let Some(ref mut child) = *guard {
                        kill_tree(child);
                    }
                    *guard = None; // prevent double-kill on the second event
                }
            }
            _ => {}
        }
    });
}
