#!/usr/bin/env python3
"""Print ComfyUI models, LoRAs, and longest positive prompt embedded in a PNG."""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import Any


MODEL_EXT_RE = re.compile(
    r"\.(safetensors|ckpt|pt|pth|bin|gguf|onnx|engine)$", re.IGNORECASE
)
MODEL_INPUT_KEYS = {
    "ckpt_name",
    "checkpoint_name",
    "unet_name",
    "vae_name",
    "clip_name",
    "clip_name1",
    "clip_name2",
    "clip_name3",
    "model_name",
    "control_net_name",
    "controlnet_name",
    "style_model_name",
    "gligen_name",
    "hypernetwork_name",
    "ipadapter_file",
}
TEXT_NODE_TYPES = (
    "CLIPTextEncode",
    "T5TextEncode",
    "TextEncode",
)


def read_png_text_chunks(path: Path) -> dict[str, str]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG file")

    chunks: dict[str, str] = {}
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        ctype = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length

        if ctype == b"tEXt":
            key, value = payload.split(b"\x00", 1)
            chunks[key.decode("latin-1")] = value.decode("utf-8", errors="replace")
        elif ctype == b"zTXt":
            key, rest = payload.split(b"\x00", 1)
            compression_method = rest[0]
            if compression_method == 0:
                chunks[key.decode("latin-1")] = zlib.decompress(rest[1:]).decode(
                    "utf-8", errors="replace"
                )
        elif ctype == b"iTXt":
            parts = payload.split(b"\x00", 5)
            if len(parts) != 6:
                continue
            key, compressed, method, _lang, _translated, text = parts
            if compressed == b"\x01" and method == b"\x00":
                text = zlib.decompress(text)
            chunks[key.decode("utf-8", errors="replace")] = text.decode(
                "utf-8", errors="replace"
            )

    return chunks


def load_comfy_json(chunks: dict[str, str], key: str) -> Any | None:
    value = chunks.get(key)
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"PNG chunk {key!r} is not valid JSON: {exc}") from exc


def node_items(prompt: Any) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(prompt, dict):
        return []
    return [
        (str(node_id), node)
        for node_id, node in prompt.items()
        if isinstance(node, dict) and isinstance(node.get("inputs"), dict)
    ]


def walk_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            found.extend(walk_strings(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_strings(child))
    return found


def is_loader_node(class_type: str) -> bool:
    lowered = class_type.lower()
    return "loader" in lowered or "load" in lowered or "lora" in lowered


def is_lora_node(name: str) -> bool:
    lowered = name.lower()
    return "lora" in lowered and "note" not in lowered and "markdown" not in lowered


def is_disabled_lora_container(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("on", "enabled", "active", "is_enabled"):
        raw = value.get(key)
        if isinstance(raw, bool) and not raw:
            return True
    return False


def is_lora_field(field: str) -> bool:
    lowered = field.lower()
    return "lora" in lowered and "add lora" not in lowered


def strength_fields(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keys = (
        "strength",
        "strength_model",
        "strength_clip",
        "model_strength",
        "clip_strength",
    )
    return {key: value[key] for key in keys if key in value}


def add_lora(
    loras: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    *,
    node: str,
    class_type: str,
    field: str,
    lora: str,
    strengths: dict[str, Any] | None = None,
) -> None:
    lora = lora.strip()
    if not lora or lora.lower() in {"none", "null", "undefined"}:
        return
    item: dict[str, Any] = {
        "node": node,
        "class_type": class_type,
        "field": field,
        "lora": lora,
    }
    if strengths:
        item.update(strengths)
    dedupe_key = (item["node"], item["class_type"], item["lora"])
    if dedupe_key not in seen:
        seen.add(dedupe_key)
        loras.append(item)


def collect_lora_strings(
    value: Any,
    *,
    node: str,
    class_type: str,
    field: str,
    loras: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    parent: Any = None,
) -> None:
    if is_disabled_lora_container(value):
        return

    if isinstance(value, str):
        if MODEL_EXT_RE.search(value):
            add_lora(
                loras,
                seen,
                node=node,
                class_type=class_type,
                field=field,
                lora=value,
                strengths=strength_fields(parent),
            )
        return

    if isinstance(value, dict):
        for key, child in value.items():
            child_field = f"{field}.{key}" if field else str(key)
            if isinstance(child, str) and is_lora_field(str(key)):
                add_lora(
                    loras,
                    seen,
                    node=node,
                    class_type=class_type,
                    field=child_field,
                    lora=child,
                    strengths=strength_fields(value),
                )
            else:
                collect_lora_strings(
                    child,
                    node=node,
                    class_type=class_type,
                    field=child_field,
                    loras=loras,
                    seen=seen,
                    parent=value,
                )
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            collect_lora_strings(
                child,
                node=node,
                class_type=class_type,
                field=f"{field}[{index}]",
                loras=loras,
                seen=seen,
                parent=parent,
            )


def extract_models(prompt: Any) -> list[dict[str, str]]:
    models: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for node_id, node in node_items(prompt):
        class_type = str(node.get("class_type", ""))
        if is_lora_node(class_type):
            continue
        inputs = node["inputs"]

        for key, value in inputs.items():
            if isinstance(value, str):
                key_l = str(key).lower()
                from_known_key = key_l in MODEL_INPUT_KEYS
                from_loader_name = is_loader_node(class_type) and (
                    key_l.endswith("_name")
                    or key_l.endswith("_file")
                    or key_l.endswith("_path")
                )
                if from_known_key or from_loader_name or MODEL_EXT_RE.search(value):
                    item = {
                        "node": node_id,
                        "class_type": class_type,
                        "field": str(key),
                        "model": value,
                    }
                    dedupe_key = (item["node"], item["class_type"], item["model"])
                    if dedupe_key not in seen:
                        seen.add(dedupe_key)
                        models.append(item)

        # Some custom loader nodes keep LoRA/model filenames in nested widgets.
        if is_loader_node(class_type):
            for value in walk_strings(inputs):
                if MODEL_EXT_RE.search(value):
                    item = {
                        "node": node_id,
                        "class_type": class_type,
                        "field": "nested",
                        "model": value,
                    }
                    dedupe_key = (item["node"], item["class_type"], item["model"])
                    if dedupe_key not in seen:
                        seen.add(dedupe_key)
                        models.append(item)

    return models


def extract_loras(prompt: Any, workflow: Any | None = None) -> list[dict[str, Any]]:
    loras: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for node_id, node in node_items(prompt):
        class_type = str(node.get("class_type", ""))
        if not is_lora_node(class_type):
            continue
        inputs = node["inputs"]

        for key, value in inputs.items():
            key_s = str(key)
            if isinstance(value, str) and is_lora_field(key_s):
                add_lora(
                    loras,
                    seen,
                    node=node_id,
                    class_type=class_type,
                    field=key_s,
                    lora=value,
                    strengths=strength_fields(inputs),
                )
            else:
                collect_lora_strings(
                    value,
                    node=node_id,
                    class_type=class_type,
                    field=key_s,
                    loras=loras,
                    seen=seen,
                    parent=inputs,
                )

    if isinstance(workflow, dict):
        for node in workflow.get("nodes", []):
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("type", node.get("class_type", "")))
            if not is_lora_node(class_type):
                continue
            node_id = str(node.get("id", "?"))
            for key in ("widgets_values", "properties"):
                collect_lora_strings(
                    node.get(key),
                    node=node_id,
                    class_type=class_type,
                    field=key,
                    loras=loras,
                    seen=seen,
                    parent=node,
                )

    return loras


def is_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 1
        and isinstance(value[0], (str, int))
    )


def link_node_id(value: Any) -> str:
    return str(value[0])


def is_text_encode_node(node: dict[str, Any]) -> bool:
    class_type = str(node.get("class_type", ""))
    return any(token in class_type for token in TEXT_NODE_TYPES)


class PromptResolver:
    def __init__(self, prompt: Any) -> None:
        self.prompt = {str(k): v for k, v in prompt.items()} if isinstance(prompt, dict) else {}

    def node(self, node_id: str) -> dict[str, Any] | None:
        node = self.prompt.get(str(node_id))
        return node if isinstance(node, dict) else None

    def input_value(self, value: Any, seen: set[str] | None = None) -> str:
        if isinstance(value, str):
            return value
        if is_link(value):
            return self.resolve_string(link_node_id(value), seen)
        return ""

    def resolve_bool(self, value: Any, seen: set[str] | None = None) -> bool | None:
        if isinstance(value, bool):
            return value
        if not is_link(value):
            return None

        node = self.node(link_node_id(value))
        if not node:
            return None
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            return None
        raw = inputs.get("value")
        return raw if isinstance(raw, bool) else None

    def resolve_string(self, node_id: str, seen: set[str] | None = None) -> str:
        seen = set() if seen is None else seen
        node_id = str(node_id)
        if node_id in seen:
            return ""
        seen.add(node_id)

        node = self.node(node_id)
        if not node:
            return ""

        class_type = str(node.get("class_type", ""))
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            return ""

        if class_type == "StringConcatenate":
            delimiter = self.input_value(inputs.get("delimiter", ""), seen)
            keys = sorted(k for k in inputs if str(k).startswith("string_"))
            return delimiter.join(
                text for text in (self.input_value(inputs[k], seen) for k in keys) if text
            )

        for key in ("text", "value", "string", "prompt"):
            if key in inputs:
                return self.input_value(inputs[key], seen)

        return ""

    def positive_text_encode_nodes(self) -> set[str]:
        roots: list[str] = []
        for _node_id, node in node_items(self.prompt):
            inputs = node["inputs"]
            positive = inputs.get("positive")
            if is_link(positive):
                roots.append(link_node_id(positive))

        found: set[str] = set()
        for root in roots:
            self._collect_text_encode_nodes(root, found, set())
        return found

    def _collect_text_encode_nodes(
        self, node_id: str, found: set[str], seen: set[str]
    ) -> None:
        node_id = str(node_id)
        if node_id in seen:
            return
        seen.add(node_id)

        node = self.node(node_id)
        if not node:
            return
        if is_text_encode_node(node):
            found.add(node_id)
            return

        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            return

        class_type = str(node.get("class_type", "")).lower()
        if "conditioning input switch" in class_type:
            selected = self.resolve_bool(inputs.get("boolean"), seen)
            if selected is True and is_link(inputs.get("conditioning_a")):
                self._collect_text_encode_nodes(link_node_id(inputs["conditioning_a"]), found, seen)
                return
            if selected is False and is_link(inputs.get("conditioning_b")):
                self._collect_text_encode_nodes(link_node_id(inputs["conditioning_b"]), found, seen)
                return

        for value in inputs.values():
            if is_link(value):
                self._collect_text_encode_nodes(link_node_id(value), found, seen)

    def all_text_encode_nodes(self) -> set[str]:
        return {node_id for node_id, node in node_items(self.prompt) if is_text_encode_node(node)}

    def text_for_node(self, node_id: str) -> str:
        node = self.node(node_id)
        if not node:
            return ""
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            return ""
        return self.input_value(inputs.get("text", ""), set())


def extract_longest_prompt(prompt: Any) -> dict[str, Any] | None:
    resolver = PromptResolver(prompt)
    candidates = resolver.positive_text_encode_nodes() or resolver.all_text_encode_nodes()

    texts = []
    for node_id in candidates:
        text = resolver.text_for_node(node_id).strip()
        if text:
            node = resolver.node(node_id) or {}
            texts.append(
                {
                    "node": node_id,
                    "class_type": str(node.get("class_type", "")),
                    "length": len(text),
                    "text": text,
                }
            )

    if not texts:
        return None
    return max(texts, key=lambda item: item["length"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract used ComfyUI models and the longest positive prompt from a PNG."
    )
    parser.add_argument("png", type=Path, help="PNG generated by ComfyUI")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    chunks = read_png_text_chunks(args.png)
    prompt = load_comfy_json(chunks, "prompt")
    if prompt is None:
        raise SystemExit("No ComfyUI 'prompt' metadata chunk found in PNG.")
    workflow = load_comfy_json(chunks, "workflow")

    result = {
        "file": str(args.png),
        "metadata_keys": sorted(chunks),
        "models": extract_models(prompt),
        "loras": extract_loras(prompt, workflow),
        "longest_prompt": extract_longest_prompt(prompt),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"File: {result['file']}")
    print(f"Metadata keys: {', '.join(result['metadata_keys'])}")
    print()
    print("Models:")
    if result["models"]:
        for item in result["models"]:
            print(
                f"- [{item['node']}] {item['class_type']}.{item['field']}: {item['model']}"
            )
    else:
        print("- none found")

    print()
    print("LoRAs:")
    if result["loras"]:
        for item in result["loras"]:
            strengths = []
            for key in (
                "strength",
                "strength_model",
                "strength_clip",
                "model_strength",
                "clip_strength",
            ):
                if key in item:
                    strengths.append(f"{key}={item[key]}")
            suffix = f" ({', '.join(strengths)})" if strengths else ""
            print(
                f"- [{item['node']}] {item['class_type']}.{item['field']}: "
                f"{item['lora']}{suffix}"
            )
    else:
        print("- none found")

    print()
    print("Longest positive text-to-image prompt:")
    longest = result["longest_prompt"]
    if longest:
        print(f"[{longest['node']}] {longest['class_type']} ({longest['length']} chars)")
        print(longest["text"])
    else:
        print("none found")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1)
