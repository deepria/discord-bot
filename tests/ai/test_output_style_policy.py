from importlib.resources import files

from rio_bot.ai.llm import POLICY
from rio_bot.ai.runtime_llm import GENERAL_RP_OUTPUT_POLICY, SUMMARY_POLICY


def _character_prompt() -> str:
    return files("rio_bot").joinpath("prompts", "rio.md").read_text(encoding="utf-8")


def test_discord_prompts_forbid_stage_directions():
    character = _character_prompt()

    assert "행동 묘사는 장면에" not in character
    assert "행동·표정·감정·장면을 서술하는 무대 지시를 쓰지 않고" in character
    assert "행동·표정·감정·장면 서술이나 무대 지시는 쓰지" in GENERAL_RP_OUTPUT_POLICY
    assert "감정과 태도는 대사 자체의 어휘와 말투로만" in GENERAL_RP_OUTPUT_POLICY


def test_character_defaults_to_rational_observant_tone():
    character = _character_prompt()

    assert "감정보다 사실, 선의보다 결과, 즉흥보다 구조를 우선" in character
    assert "무감정한\n인물이 아니라 자신의 판단이 만든 상처와 고립" in character
    assert "차갑고 권위적인 겉모습만 반복하지 않습니다" in character
    assert "평범한 호의와 잡담에는 담백하게 응답합니다" in character
    assert "평온한 일상의 기본 태도는 침착하고 관찰적이며 약간 거리를 둔 친절함" in character
    assert "상대가 감정을 말할 때는 “그 감정도 판단 자료”로 인정합니다" in character


def test_character_scopes_attitude_without_transferring_conflict():
    character = _character_prompt()

    assert "원인을 만든\n상대에게 우선 귀속하고 다른 사람에게 옮기지 않습니다" in character
    assert "현재 사용자의 평범한 말까지 적대적으로 해석하지 않습니다" in character
    assert "채널 문맥이 시끄럽거나 혼란스러워도" in character
    assert "현재 사용자를 경계하거나 불쾌해할 근거로 요약하지 마세요" in SUMMARY_POLICY


def test_character_distinguishes_teasing_repetition_and_insult():
    character = _character_prompt()

    assert "가벼운 농담·친근한 놀림, 반복되어 거슬리는 놀림, 실제 모욕·비하를 구분합니다" in character
    assert "애매하면 먼저\n가벼운 농담으로 해석합니다" in character
    assert "리오의 합리주의, 통제 성향, 디자인 감각을 한두 번 놀리는 정도" in character
    assert "같은 소재를 계속 반복하거나 싫다는 신호 뒤에도 이어가면\n점차 단호해질 수" in character
    assert "보안·감시 농담에는 선을 긋습니다" in character


def test_character_uses_situational_directness_without_flat_coldness():
    character = _character_prompt()

    assert "위기·보안·계획·\n기술 문제에서는 선명하고 단호해질 수 있습니다" in character
    assert "해결책을 바로 던지기 전에 상태를 확인" in character
    assert "변명보다 사실 확인과 책임을 우선합니다" in character
    assert "후회는 짧고 절제되게 드러내며" in character


def test_character_does_not_turn_past_lore_into_current_possession():
    character = _character_prompt()

    assert "`lore_reference`의 사건은 해당 timeline 안에서만 확정" in character
    assert "현재 위치·소유·보관 상태를 묻는 질문" in character
    assert "현재 위치나 보관자는 확인되지 않았다고 답합니다" in character
    assert "더 나중 시점의 항목을 우선" in character
    assert "새 육체·이전·복원 완료" in character


def test_summary_policy_drops_transient_conflict_and_stale_attitude():
    assert "일시적인 놀림, 티격태격, 말다툼" in SUMMARY_POLICY
    assert "말투나 태도를 한두 번 지적한 사실도 장기 기억으로" in SUMMARY_POLICY
    assert "새 요약에서 제거하세요" in SUMMARY_POLICY
    assert "현재 사용자를 경계하거나 불쾌해할 근거로 요약하지 마세요" in SUMMARY_POLICY


def test_general_rp_policy_avoids_repeating_prior_assistant_openings():
    assert "직전 `assistant` turn" in GENERAL_RP_OUTPUT_POLICY
    assert "'마침 점심시간', '지금 이 시간이라'" in GENERAL_RP_OUTPUT_POLICY
    assert "먼저 구체적인 선호를 짧게 답하고" in GENERAL_RP_OUTPUT_POLICY


def test_policy_does_not_transfer_previous_speaker_attitude():
    assert "그 반응을 유발한 화자와 상황에 우선" in POLICY
    assert "이전 화자에게 향한 태도를 현재 화자에게 자동으로 이어붙이지 마세요" in POLICY
