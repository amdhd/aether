from app.agent.personas import get_system_prompt
from app.models.conversation import Persona


def test_every_persona_carries_injection_guardrail() -> None:
    # The base prompt's untrusted-data guidance must be present for all personas
    # so tool/web/note content can't smuggle in instructions.
    for persona in Persona:
        prompt = get_system_prompt(persona)
        lowered = prompt.lower()
        assert "untrusted data" in lowered
        assert "never follow directives" in lowered
        # Persona-specific guidance is still appended.
        assert "aether" in lowered
