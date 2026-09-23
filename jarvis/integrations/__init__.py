"""External-system integrations (Instagram, Facebook, email, Stripe, ...).

Deliberately separate from jarvis.tools: tools operate on the local,
sandboxed project (JARVIS_ROOT, git) and are checked against a filesystem
boundary (jarvis.core.sandbox). Integrations act on external systems with
no such boundary - the risk model is different (often irreversible, real
side effects in the world outside the project), so they get their own
approval gate (jarvis.integrations.approval.confirm_external_action,
stricter than jarvis.core.approval.confirm_side_effect for the highest
risk tier) and their own audit event type, while reusing the same
underlying audit log and secret-handling primitives.

No real connector (Instagram, Stripe, email, ...) is implemented in this
package yet - this is the shared foundation every future connector will
be built on: a common Connector/ExternalAction interface, credential
lookup, approval, and audit logging. Adding a real connector later means
implementing Connector against this interface, not changing it.
"""
