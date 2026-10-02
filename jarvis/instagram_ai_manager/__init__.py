"""Instagram AI Manager: a modular content-generation and analytics
feature layered on top of JARVIS's existing Instagram integration
(jarvis.integrations.connectors.instagram.InstagramConnector) and LLM
client (jarvis.core.llm.LLMClient) - it adds no new Instagram API
surface and no new write/publish capability of its own.

Two sub-modules, per the module's own architecture requirement (keep AI
logic separate from UI components, and separate from data storage):

  - jarvis.instagram_ai_manager.db: SQLite storage for generated
    content (see that module's own docstring for the schema).
  - jarvis.instagram_ai_manager.ai_services: isolated, tool-free LLM
    calls that generate Reel ideas/hooks/captions/CTAs/hashtags/Story
    sequences/weekly plans - the same "one prompt, no tools, no
    conversation history" pattern jarvis.core.commit_message already
    uses, so a generation call can never trigger a real Instagram
    action (there are no tools to call).
  - jarvis.instagram_ai_manager.analytics_services: read-only analysis
    functions over REAL data already available from InstagramConnector/
    jarvis.integrations.instagram_history - never fabricates a metric
    or claims a pattern the underlying data doesn't support.

No UI code lives in this package - jarvis.gui.views.* views (added in a
later stage of this feature) call into these two service modules and
render their results; this package has no dependency on customtkinter
or any other GUI library.
"""
