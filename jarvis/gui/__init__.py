"""Desktop GUI for JARVIS (jarvis.gui.app.JarvisApp), built on
customtkinter. Wraps the EXISTING jarvis.core.agent.Agent,
jarvis.voice.speech_to_text/text_to_speech, and every tool/connector
already implemented - this package adds no new capability of its own,
only a window around what already works, exactly the way
jarvis.voice.voice_loop already wraps Agent.step()/listen_once()/speak()
for the terminal 'voice' REPL command. jarvis.cli.main (the terminal
REPL) is completely unmodified and unaffected by this package existing.
"""
