"""Text tools for free-form chat sessions (no video). Registered under 'text'."""
import ast
import operator
import re
from typing import Dict, List

from core.tools import ToolContext, ToolResult, tool

MAX_HITS = 5

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    raise ValueError("only numbers, + - * / // % ** and parentheses are allowed")


@tool("text", "calculate", "Evaluate an arithmetic expression, e.g. '(12.5 - 3) / 4'.",
      {"type": "object", "required": ["expression"],
       "properties": {"expression": {"type": "string"}}})
def calculate(ctx: ToolContext, expression):
    try:
        value = _eval(ast.parse(expression.strip(), mode="eval"))
    except ZeroDivisionError:
        return ToolResult(text="Division by zero.", is_error=True)
    except (ValueError, SyntaxError) as e:
        return ToolResult(text=f"Cannot evaluate: {e}", is_error=True)
    return ToolResult(text=f"{expression.strip()} = {value}", data={"value": value})


def _messages(session: Dict) -> List[Dict]:
    out = []
    for turn in session.get("turns", []):
        if turn.get("question"):
            out.append({"turn": turn["turn_index"], "role": "reviewer", "text": turn["question"]})
        if turn.get("answer"):
            out.append({"turn": turn["turn_index"], "role": "assistant", "text": turn["answer"]})
    return out


@tool("text", "search_history",
      "Search earlier messages in this conversation for a word or phrase. Use it when the "
      "reviewer refers to something said before.",
      {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}})
def search_history(ctx: ToolContext, query):
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    if not words:
        return ToolResult(text="Query too short.", is_error=True)
    scored = []
    for m in _messages(ctx.session):
        hits = sum(w in m["text"].lower() for w in words)
        if hits:
            scored.append((hits, m))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return ToolResult(text="No earlier message matches.")
    lines = [f"- turn {m['turn']} ({m['role']}): {m['text'][:300]}" for _, m in scored[:MAX_HITS]]
    return ToolResult(text="\n".join(lines), data={"matches": len(scored)})


@tool("text", "ask_user",
      "Ask the reviewer one clarifying question instead of guessing. Ends your turn.",
      {"type": "object", "required": ["question"],
       "properties": {"question": {"type": "string"}}})
def ask_user(ctx: ToolContext, question):
    return ToolResult(text=question, data={"text": question, "needs_reply": True},
                      end_turn=True)
