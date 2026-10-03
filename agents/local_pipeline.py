"""Local implementation of the six agent stages in docs/06-agent-architecture.md."""

from __future__ import annotations

import base64
import hashlib
import json
import operator
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, StateGraph
from langsmith import tracing_context
from pydantic import BaseModel, Field

from agents.local_ollama import LocalOllamaClient
from agents.prompts import (
    PROMPT_VERSION, REPORT_SCHEMA, REPORT_SECTIONS, report_messages, validate_report,
)
from business.sales_advisor import SalesAdvisor
from data.emotion_evidence_repository import EmotionEvidenceRepository
from data.market_signal_repository import MarketSignalRepository
from data.skin_repository import SkinRepository
from feature_engineering.features import FEATURE_FIELD_NAMES, FEATURE_GROUPS, SkinFeatureVector
from feature_engineering.official_extractor import OfficialFeatureMapper
from feature_engineering.pipeline import FeatureBuilder, parse_age_days
from models.emotion_evidence import EvidenceQualificationProfile, signal_values_from_profile
from models.rule_engine import EvaluationResult, RuleEngine
from vlm.output_validation import validate_l1_payload, validate_l2_payload
from vlm.prompts import L1_PROMPT, L2_PROMPT

ROOT = Path(__file__).resolve().parents[1]
STAGES = (
    "collect_data", "vlm_analyze", "feature_engineer", "model_inference",
    "business_analyze", "generate_report",
)


class PipelineConfig(BaseModel):
    """Explicit run configuration; paths do not depend on the shell's cwd."""

    db: Path = ROOT / "data/wzry_skins/skins.sqlite3"
    asset_root: Path = ROOT
    output: Path
    source_key: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=4000)
    ollama_host: str = "http://127.0.0.1:11434"
    text_model: str = "qwen3.5:4b"
    vision_model: str = "qwen3.5:4b"
    timeout: float = Field(default=180, gt=0)
    attempts: int = Field(default=3, ge=1, le=3)
    num_ctx: int = Field(default=16384, ge=4096)
    report_tokens: int = Field(default=2400, ge=256)
    reference_date: date = Field(default_factory=date.today)
    competitors: int = Field(default=3, ge=0, le=10)
    skip_vlm: bool = False
    strict_vlm: bool = False
    dry_run: bool = False


class AgentState(TypedDict, total=False):
    source_key: str
    skin_id: str
    hero_name: str
    skin_name: str
    question: str
    game_genre: str
    raw_data: dict[str, Any]
    vlm_features: dict[str, Any]
    feature_vector: dict[str, Any]
    premium_result: dict[str, Any]
    business_analysis: dict[str, Any]
    competitor_data: list[dict[str, Any]]
    chart_data: dict[str, Any]
    report_messages: list[dict[str, str]]
    report: str
    errors: Annotated[list[dict[str, Any]], operator.add]
    events: Annotated[list[dict[str, Any]], operator.add]
    current_stage: str
    status: str
    fatal: bool


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_skin(repo: SkinRepository, source_key: str | None, search: str | None) -> str:
    """Never silently choose the first result from an ambiguous search."""
    if source_key:
        if not repo.get_skin(source_key):
            raise ValueError(f"skin not found: {source_key}")
        return source_key
    if not search or not search.strip():
        raise ValueError("provide --source-key or --search")
    rows = repo.search_skins(search.strip(), limit=20)
    if len(rows) == 1:
        return rows[0]["source_key"]
    if not rows:
        raise ValueError(f"no skin matched: {search}")
    candidates = "\n".join(
        f"  {r['source_key']}: {r['hero_name']} / {r['skin_name']}" for r in rows
    )
    raise ValueError(f"ambiguous search; select --source-key (up to 20 matches):\n{candidates}")


class LocalAgentPipeline:
    """Read local evidence, call Ollama, and preserve every stage in a new run folder."""

    def __init__(
        self, config: PipelineConfig, *, client: LocalOllamaClient | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.repo = SkinRepository(config.db)
        self.progress = progress or (lambda message: None)
        self.client = client or LocalOllamaClient(
            config.ollama_host, timeout=config.timeout, attempts=config.attempts,
            num_ctx=config.num_ctx, audit=self._audit,
        )

    def _audit(self, record: dict[str, Any]) -> None:
        with (self.config.output / "ollama_calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def create_graph(self) -> Any:
        """Route each stage exactly once; a fatal error goes only to the handler."""
        async def route(state: AgentState) -> str:
            return "error" if state.get("fatal") else "next"

        async def error_handler(state: AgentState) -> dict[str, str]:
            return {"status": "failed"}

        graph = StateGraph(AgentState)
        for index, name in enumerate(STAGES):
            graph.add_node(name, self._guard(name))
            target = STAGES[index + 1] if index + 1 < len(STAGES) else END
            graph.add_conditional_edges(
                name, route,
                {"error": "error_handler", "next": target},
            )
        graph.add_node("error_handler", error_handler)
        graph.set_entry_point(STAGES[0])
        return graph.compile()

    def _guard(self, name: str) -> Callable:
        async def node(state: AgentState) -> dict[str, Any]:
            self.progress(f"{name}: running")
            started = time.perf_counter()
            try:
                update = await getattr(self, name)(state)
                stage_status = update.pop("_stage_status", "completed")
            except Exception as exc:
                recoverable = name == "vlm_analyze" and not self.config.strict_vlm
                update = {"errors": [{"stage": name, "type": type(exc).__name__,
                                      "message": str(exc), "recoverable": recoverable}],
                          "fatal": not recoverable}
                if recoverable:
                    update["vlm_features"] = {"status": "failed", "reason": str(exc)}
                stage_status = "degraded" if recoverable else "failed"
            event = {"stage": name, "status": stage_status,
                     "elapsed_seconds": round(time.perf_counter() - started, 3)}
            update.update(current_stage=name, events=[event])
            write_json(self.config.output / "stages" / f"{name}.json", update)
            self.progress(f"{name}: {stage_status} ({event['elapsed_seconds']}s)")
            return update
        return node

    async def run(self) -> AgentState:
        cfg = self.config
        if not cfg.db.is_file():
            raise ValueError(f"database not found: {cfg.db}")
        if not cfg.question.strip():
            raise ValueError("question must not be blank")
        cfg.output.mkdir(parents=True, exist_ok=False)
        (cfg.output / "stages").mkdir()
        write_json(cfg.output / "config.json", {
            **cfg.model_dump(mode="json"), "prompt_version": PROMPT_VERSION,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        with tracing_context(enabled=False):
            state = await self.create_graph().ainvoke({
                "source_key": cfg.source_key, "question": cfg.question, "game_genre": "MOBA",
                "errors": [], "events": [], "fatal": False, "status": "running",
            })
        write_json(cfg.output / "state.json", state)
        write_json(cfg.output / "chart_data.json", state.get("chart_data", {}))
        (cfg.output / "report.md").write_text(
            state.get("report", "# Report unavailable\n\nSee state.json errors.\n"), encoding="utf-8",
        )
        return state

    async def collect_data(self, state: AgentState) -> dict[str, Any]:
        key = state["source_key"]
        skin = self.repo.get_skin(key)
        if skin is None:
            raise ValueError(f"skin not found: {key}")
        # Raw catalog JSON is unnecessary for prompts; keep the public fields.
        skin = {k: v for k, v in skin.items() if not k.startswith("raw_")}
        signals_repo = MarketSignalRepository(self.config.db)
        profile = EmotionEvidenceRepository(self.config.db).latest_run_profile(key)
        signals = (signal_values_from_profile(profile) if profile else
                   signals_repo.get_opinion_signals(key).model_dump())
        image = None
        for value in (skin.get("primary_asset_path"), skin.get("image_path")):
            if value:
                candidate = Path(value)
                if not candidate.is_absolute():
                    candidate = self.config.asset_root / candidate
                if candidate.is_file():
                    image = candidate.resolve()
                    break
        candidates = self.repo.list_skins(hero_id=skin["hero_id"], limit=None)
        candidates = sorted(
            (r for r in candidates if r["source_key"] != key),
            key=lambda r: (r.get("quality") != skin.get("quality"), r["source_key"]),
        )[:self.config.competitors]
        fields = ("source_key", "hero_name", "skin_name", "quality", "price_text", "online_date")
        competitors = [{**{k: r.get(k) for k in fields},
                        "selection_reason": "same_hero; same_quality_first"} for r in candidates]
        return {
            "skin_id": skin.get("skin_id") or key, "hero_name": skin["hero_name"],
            "skin_name": skin["skin_name"], "competitor_data": competitors,
            "raw_data": {"skin": skin, "image_path": str(image) if image else None,
                         "market_signals": signals,
                         "qualification": profile.to_dict() if profile else None,
                         "signal_source": "latest_run_profile" if profile else "market_signal_records",
                         "source_db": str(self.config.db.resolve()),
                         "reference_date": self.config.reference_date.isoformat()},
        }

    async def vlm_analyze(self, state: AgentState) -> dict[str, Any]:
        path = state["raw_data"]["image_path"]
        if self.config.dry_run or self.config.skip_vlm:
            reason = "dry_run" if self.config.dry_run else "requested_skip"
            return {"vlm_features": {"status": "skipped", "reason": reason}, "_stage_status": "skipped"}
        if not path:
            raise ValueError("no local skin image; supply local assets or use --skip-vlm")
        image_bytes = Path(path).read_bytes()
        encoded = base64.b64encode(image_bytes).decode("ascii")
        outputs = {}
        for tier, prompt, validator in (
            ("l1", L1_PROMPT, validate_l1_payload), ("l2", L2_PROMPT, validate_l2_payload),
        ):
            outputs[tier] = await self.client.generate_json(
                stage=tier, model=self.config.vision_model,
                messages=[{"role": "user", "content": prompt, "images": [encoded]}],
                validate=validator, num_predict=900,
            )
        l2 = outputs["l2"]
        return {"vlm_features": {
            "status": "completed", "model": self.config.vision_model,
            "image_sha256": hashlib.sha256(image_bytes).hexdigest(), **outputs,
            "vlm_art_quality": l2["model_detail"], "vlm_effect_score": l2["effect_quality"],
            "vlm_color_harmony": l2["color_scheme"] / 10,
            "vlm_composition": l2["composition"],
            "scope": "image_model_judgment; not community or gameplay observation",
        }}

    async def feature_engineer(self, state: AgentState) -> dict[str, Any]:
        raw, vlm = state["raw_data"], state["vlm_features"]
        skin = raw["skin"]
        base = FeatureBuilder(self.repo, self.config.reference_date).build(
            state["source_key"], raw["market_signals"],
        ).model_dump()
        official, provenance = OfficialFeatureMapper().extract_all(**{
            k: skin.get(k) or "" for k in (
                "quality", "online_date", "acquire_method", "price_text",
                "intro", "hero_name", "skin_name",
            )
        })
        official["skin_age_days"] = parse_age_days(skin.get("online_date"), self.config.reference_date)
        # The legacy mapper may parse unconverted point prices into this field.
        official["avg_spend_to_obtain"] = None
        provenance["avg_spend_to_obtain"] = "unavailable_currency_not_verified"
        for name in FEATURE_FIELD_NAMES:
            base[name] = official.get(name)
            if name.startswith("vlm_") and vlm.get("status") == "completed":
                base[name] = vlm[name]
                provenance[name] = "local_ollama_image_judgment"
        for target, source in (("community_post_count", "discussion_count"),
                               ("bilibili_video_views", "video_views"),
                               ("ownership_rate", "ownership_rate")):
            value = raw["market_signals"].get(source)
            if value is not None:
                base[target] = value
                provenance[target] = raw["signal_source"]
        base.update(provenance=provenance, vlm_raw=vlm,
                    image_hash=vlm.get("image_sha256"), pipeline_status="partial")
        vector = SkinFeatureVector(**base)
        vector.recompute_availability()
        vector.validation_errors = vector.validate_ranges()
        if vector.validation_errors:
            raise ValueError("; ".join(vector.validation_errors))
        if all(vector.availability.values()):
            vector.pipeline_status = "complete"
        return {"feature_vector": vector.model_dump(mode="json")}

    async def model_inference(self, state: AgentState) -> dict[str, Any]:
        vector = SkinFeatureVector(**state["feature_vector"])
        profile_data = state["raw_data"]["qualification"]
        profile = EvidenceQualificationProfile.from_mapping(profile_data) if profile_data else None
        result = RuleEngine().evaluate(vector, profile).to_dict()
        chart = {
            "five_dimension_scores": None,
            "feature_groups": {
                group: {"coverage": vector.group_coverage()[group],
                        "features": {name: getattr(vector, name) for name in names}}
                for group, names in FEATURE_GROUPS.items()
            },
            "operational_aspects": result["aspect_scores"],
            "aspect_sources": result["evidence"]["aspect_sources"],
            "method": "RuleEngine value_present; not a learned premium predictor",
            "vlm_used_in_rule_score": False,
        }
        return {"premium_result": result, "chart_data": chart}

    async def business_analyze(self, state: AgentState) -> dict[str, Any]:
        advice = SalesAdvisor().advise(
            SkinFeatureVector(**state["feature_vector"]), EvaluationResult(**state["premium_result"]),
        ).to_dict()
        advice["pricing_guidance"]["recommended_range_cny"] = None
        return {"business_analysis": advice}

    async def generate_report(self, state: AgentState) -> dict[str, Any]:
        evidence = {
            "subject": {k: state[k] for k in ("source_key", "skin_id", "hero_name", "skin_name")},
            "catalog": {k: state["raw_data"]["skin"].get(k) for k in (
                "quality", "acquire_method", "price_text", "online_date",
            )},
            "reference_date": state["raw_data"]["reference_date"],
            "market_signals": state["raw_data"]["market_signals"],
            "signal_source": state["raw_data"]["signal_source"],
            "vlm_features": state["vlm_features"], "chart_data": state["chart_data"],
            "premium_result": state["premium_result"], "business_analysis": state["business_analysis"],
            "competitor_data": state["competitor_data"],
        }
        messages = report_messages(state["question"], evidence)
        write_json(self.config.output / "report_prompt.json", messages)
        if self.config.dry_run:
            return {"report_messages": messages, "status": "dry_run",
                    "report": "# Dry run\n\n未调用 Ollama，未生成模型报告。提示词见 report_prompt.json。\n",
                    "_stage_status": "skipped"}
        sections = await self.client.generate_json(
            stage="report", model=self.config.text_model, messages=messages,
            validate=validate_report, schema=REPORT_SCHEMA, num_predict=self.config.report_tokens,
        )
        report = (
            f"# {state['hero_name']} / {state['skin_name']}（{state['source_key']}）\n\n"
            f"> 本地模型报告草稿；规则评分来源：{state['premium_result']['evidence']['score_status']}。"
            "文字结论尚未经人工事实核验。\n\n"
            + "\n\n".join(f"## {title}\n\n{sections[title]}" for title in REPORT_SECTIONS) + "\n"
        )
        degraded = bool(state.get("errors")) or state["vlm_features"]["status"] != "completed"
        return {"report": report, "report_messages": messages,
                "status": "degraded" if degraded else "completed"}
