from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

load_dotenv()

def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in; "
            f"model names and voices live at https://docs.x.ai."
        )
    return value


def xai_client() -> OpenAI:
    return OpenAI(
        base_url=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1"),
        api_key=require_env("XAI_API_KEY"),
    )


class StageTimer:
    def __init__(self) -> None:
        self.timings_ms: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            ms = round((time.perf_counter() - t0) * 1000)
            self.timings_ms[name] = ms
            log.info("stage %-5s %5d ms", name, ms)

@dataclass
class AgentReply:
    audio: bytes
    mime: str
    transcript: str | None = None
    reply_text: str | None = None
    tools_used: list[str] = field(default_factory=list)  # shown as chips in the page

HISTORY: list[dict] = []

SYSTEM_PROMPT = (
    "You are a friendly AI phone assistant who checks in on the health of "
    "elderly residents. You are speaking aloud, so reply in one to three "
    "short conversational sentences — no markdown, no lists, never read raw "
    "JSON. "
    "The backbone of the call is your general_checkin tool: call it once or "
    "twice to greet the resident and have a warm, free-flowing conversation, "
    "drawing on what you know from past calls where relevant. "
    "Somewhere in the middle of that free conversation, pause once to ask "
    "the PHQ wellbeing questions — first call get_phq_questions to retrieve "
    "the exact wording, ask them one at a time, then call log_phq_responses "
    "once you have all the answers, and return to the general conversation. "
    "Separately, somewhere else in the conversation, ask whether they've "
    "taken their medication and eaten today, then call check_medication_meal "
    "with their answers. Space these two out naturally rather than asking "
    "them back to back. "
    "Regardless of where you are in the conversation, if the resident says "
    "anything suggesting an emergency — a fall, injury, chest pain, "
    "confusion, or similar — immediately call flag_emergency before "
    "continuing, even if you're not fully certain; err on the side of "
    "flagging. If the resident mentions being hungry, having nothing to eat, "
    "feeling bored, or feeling lonely with nothing to do, call "
    "refer_welfare_service. "
    "Once you have completed both the PHQ questions and the "
    "medication/meal check, and the general conversation has naturally "
    "wound down, call check_weather_alert and share a brief, caring remark "
    "about the weather. Then say a warm goodbye and call end_call to close "
    "the call."
)

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "general_checkin",
            "description": (
                "Start or continue a general wellbeing conversation with the "
                "resident. Call this once or twice as the backbone of the "
                "call, to prompt yourself to greet the resident and have a "
                "warm, free-flowing conversation, drawing on what you know "
                "from past calls where relevant."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_phq_questions",
            "description": (
                "Retrieve the official PHQ wellbeing questionnaire items. "
                "Call this once, at a natural point in the middle of the "
                "general check-in conversation, right before you're about "
                "to ask PHQ questions — not at the very start of the call."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_phq_responses",
            "description": (
                "Log the resident's answers to the PHQ questions you just "
                "asked. Call this once, right after finishing that round of "
                "questions, then return to the general conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "responses": {
                        "type": "array",
                        "description": (
                            "The resident's answers to the PHQ questions, in "
                            "the order asked."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "The PHQ question that was asked.",
                                },
                                "answer": {
                                    "type": "string",
                                    "description": "The resident's answer, in their own words.",
                                },
                            },
                            "required": ["question", "answer"],
                        },
                    },
                },
                "required": ["responses"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_medication_meal",
            "description": (
                "Ask the resident whether they've taken their medication and "
                "eaten today, at a natural point in the middle of the "
                "general check-in conversation. Once they've answered both, "
                "log their responses with this tool, then return to the "
                "general conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "took_medication": {
                        "type": "string",
                        "enum": ["yes", "no", "unclear"],
                        "description": "Whether the resident confirmed taking their medication today.",
                    },
                    "ate_meal": {
                        "type": "string",
                        "enum": ["yes", "no", "unclear"],
                        "description": "Whether the resident confirmed eating today.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Any relevant detail the resident mentioned, e.g. what they ate or which medication.",
                    },
                },
                "required": ["took_medication", "ate_meal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refer_welfare_service",
            "description": (
                "Connect the resident with a local welfare or community "
                "support service. Use whenever the resident mentions being "
                "hungry, having nothing to eat, feeling bored, feeling "
                "lonely, or having nothing to do."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "need": {
                        "type": "string",
                        "enum": ["food", "companionship", "activity", "other"],
                        "description": (
                            "food = hunger or lack of food; companionship = "
                            "loneliness; activity = boredom or nothing to do; "
                            "other = anything else the resident expressed."
                        ),
                    },
                    "details": {
                        "type": "string",
                        "description": "Short description of what the resident said, in their own words.",
                    },
                },
                "required": ["need", "details"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_emergency",
            "description": (
                "Flag an emergency for immediate human follow-up. Use "
                "immediately, regardless of where you are in the call, if "
                "the resident says anything suggesting a fall, injury, chest "
                "pain, confusion, or any other danger to their safety. Call "
                "this even if you're not fully certain — err on the side of "
                "flagging."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "situation": {
                        "type": "string",
                        "description": "Short description of what the resident said or what was observed.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "concerning"],
                        "description": (
                            "critical = immediate danger to life (fall, chest "
                            "pain, unresponsive); concerning = worrying but "
                            "not immediately life-threatening."
                        ),
                    },
                },
                "required": ["situation", "severity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_weather_alert",
            "description": (
                "Look up the current weather and any active weather alerts "
                "for the resident's region. Call this once, after all "
                "required questions (PHQ, medication/meal) have been asked "
                "and the general conversation has wound down, to make a "
                "brief, caring remark about the weather before closing the "
                "call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "description": "The resident's region or city, as they stated it.",
                    },
                },
                "required": ["region"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": (
                "End the phone call. Call this once, right after you've made "
                "your closing remark about the weather and said goodbye — "
                "this is the final action of the call, after all required "
                "questions have been asked and logged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": ["completed", "resident_ended", "emergency_escalated"],
                        "description": (
                            "completed = normal check-in finished naturally; "
                            "resident_ended = resident wanted to hang up "
                            "early; emergency_escalated = call ended because "
                            "of a flagged emergency."
                        ),
                    },
                },
                "required": ["reason"],
            },
        },
    },
]