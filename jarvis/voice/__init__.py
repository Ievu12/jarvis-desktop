"""Voice mode: microphone-in, speaker-out conversational wrapper around
the existing text-based jarvis.core.agent.Agent - see voice_loop.py for
the conversation loop, speech_to_text.py for microphone -> Lithuanian
text, and text_to_speech.py for text -> spoken Lithuanian audio. This
package does not modify Agent, any Tool, or any Connector (Instagram
included) - it only calls Agent.step() exactly like
jarvis.cli.main._run_agent_turn() already does for typed input."""
