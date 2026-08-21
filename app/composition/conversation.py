from typing import Any

from app.composition.research import compose_jarvis_swing_research
from app.conversation.config import JarvisConversationConfig
from app.conversation.events import ConversationEventSink
from app.conversation.session import JarvisConversationSession
from app.use_cases.pull_rolling_market_series import PullRollingMarketSeries


def compose_jarvis_conversation(
    rolling_fetch: PullRollingMarketSeries,
    *,
    conversation_config: JarvisConversationConfig | None = None,
    conversation_event_sink: ConversationEventSink | None = None,
    **research_dependencies: Any,
) -> JarvisConversationSession:
    """Compose text/voice activation around the lazy research workflow."""
    research = compose_jarvis_swing_research(
        rolling_fetch,
        **research_dependencies,
    )
    return JarvisConversationSession(
        research,
        conversation_config or JarvisConversationConfig.from_environment(),
        event_sink=conversation_event_sink,
    )

