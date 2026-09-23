"""Dashboard panel views for jarvis.gui.app.JarvisApp - one module per
sidebar nav item (home, chat, tasks, instagram, gmail, stripe, content,
analytics, automations, settings). Each view is a customtkinter Frame
subclass that renders data supplied by jarvis.gui.dashboard_data (or,
for Chat/Settings, delegates directly to widgets JarvisApp itself owns -
see jarvis.gui.app's module docstring for why those two stay on
JarvisApp rather than becoming their own view classes). No view module
calls JARVIS core/integrations/tools directly - all data comes through
dashboard_data or JarvisApp's existing agent/worker plumbing.
"""
