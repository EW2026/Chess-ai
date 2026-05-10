from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.health),
    path("local-token/", views.local_token),
    path("train/", views.train),
    path("ai-move/", views.ai_move),
    path("player-stats/", views.player_stats),
    path("warmup-pool/", views.warmup_pool),
    path("new-game/", views.new_game),
    path("issue-log/", views.issue_log),
]


