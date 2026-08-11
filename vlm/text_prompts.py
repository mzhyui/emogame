"""Text-generation prompt templates for Weibo comment synthesis.

Separate from ``vlm/prompts.py`` (which holds vision prompts for L1/L2/L3).
These templates drive the few-shot, skin-contextualized comment generator.
"""

from __future__ import annotations

from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Prompt templates
# ═══════════════════════════════════════════════════════════════════════════

WEIBO_STYLE_SYSTEM = """\
你是一个王者荣耀玩家，正在微博上评论游戏内容。你的评论应该：
- 长度 10-140 字，自然口语化中文
- 可以包含表情符号，但不要全表情
- 聚焦皮肤的某个具体方面（特效、建模、手感、价格、原画、语音、限定标签等）
- 不要使用 markdown，不要英文，不要列表
- 不要重复相同的句式
- 可以表达期待、吐槽、赞美、犹豫等真实情绪
- 偶尔可以 @王者荣耀 或提及其他玩家"""

WEIBO_SKIN_CONTEXT = """\
皮肤信息：{hero_name} - {skin_name}（{quality}）\
{extra_info}"""

WEIBO_FEW_SHOT_TEMPLATE = """\
以下是该皮肤相关的真实评论风格参考（不要照抄，只参考风格）：
{examples}"""


# ═══════════════════════════════════════════════════════════════════════════
# Prompt builder
# ═══════════════════════════════════════════════════════════════════════════


def build_generation_prompt(
    skin_meta: dict[str, Any],
    examples: list[str],
    style_spec: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Build the (system, user_prompt) pair for one generation call.

    Parameters
    ----------
    skin_meta : dict
        Must contain ``hero_name``, ``skin_name``, ``quality``.
        Optional: ``release_status``, ``price_tier``, ``acquire_method``.
    examples : list[str]
        Real comments to use as few-shot style references.
    style_spec : dict, optional
        Corpus-wide style statistics (avg_len, emoji_rate, etc.).
        Currently unused but reserved for future prompt refinement.

    Returns
    -------
    tuple[str, str]
        (system_prompt, user_prompt) ready to pass to
        ``OllamaClient.chat_text()``.
    """
    # Build system prompt (style constraints)
    system = WEIBO_STYLE_SYSTEM

    # Build extra info line for skin context
    extra_parts: list[str] = []
    if skin_meta.get("release_status"):
        extra_parts.append(f"上线状态：{skin_meta['release_status']}")
    if skin_meta.get("price_tier"):
        extra_parts.append(f"价格档位：{skin_meta['price_tier']}")
    if skin_meta.get("acquire_method"):
        extra_parts.append(f"获取方式：{skin_meta['acquire_method']}")

    extra_info = ""
    if extra_parts:
        extra_info = "\n" + "\n".join(extra_parts)

    # Build user prompt (skin context + few-shot examples + instruction)
    skin_context = WEIBO_SKIN_CONTEXT.format(
        hero_name=skin_meta["hero_name"],
        skin_name=skin_meta["skin_name"],
        quality=skin_meta["quality"],
        extra_info=extra_info,
    )

    examples_text = "\n".join(f"- {ex}" for ex in examples) if examples else "(暂无参考评论)"
    few_shot = WEIBO_FEW_SHOT_TEMPLATE.format(examples=examples_text)

    user_prompt = f"{skin_context}\n\n{few_shot}\n\n请写一条微博评论。只输出评论文本，不要任何解释。"

    return system, user_prompt
