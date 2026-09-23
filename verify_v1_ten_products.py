#!/usr/bin/env python3
"""Exercise the real V1 scheduler with ten distinct ready products."""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
import uuid

os.environ["V1_DISABLE_AUTOSTART"] = "1"
import v1_api  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_v1_ten_products.py SOURCE_SESSION_ID")
    source_id = sys.argv[1]
    source = json.loads((v1_api.V1_DIR / source_id / "session.json").read_text(encoding="utf-8"))
    base_item = next(item for item in source["queue"] if item.get("render_status") == "ready")

    names = [
        "22.5W快充移动电源", "降噪蓝牙耳机", "智能运动手表", "轻薄平板电脑", "磁吸手机壳",
        "Type-C数据线", "便携蓝牙音箱", "高清自拍杆", "无线充电器", "多口充电插座",
    ]
    queue = []
    required = {"welcome", "point_product", "price", "discount", "buy_now", "next_product"}
    for index, name in enumerate(names):
        product = {
            "sku": f"ten-{index + 1}", "name": name, "link": f"https://example.invalid/products/{index + 1}",
            "original_price": str(99 + index * 10), "sale_price": str(79 + index * 8),
            "selling_points": f"{name}的核心卖点与使用场景", "params": f"第{index + 1}款商品参数",
            "promotion": "直播间限时优惠",
        }
        plan = v1_api._fallback_plan(product, index < len(names) - 1)
        actions = {segment["action"] for segment in plan}
        expected = required if index < len(names) - 1 else required - {"next_product"}
        assert expected.issubset(actions), (name, actions)
        item = copy.deepcopy(base_item)
        item.update({
            "id": uuid.uuid4().hex[:16], "product": product, "duration_seconds": 15,
            "script": "".join(segment["text"] for segment in plan), "script_plan": plan,
            "render_status": "ready", "message": "十商品轮播验收片段",
        })
        queue.append(item)

    test_id = "f" * 24
    session = {
        "id": test_id, "version": "V1.0", "created_at": v1_api._now(), "updated_at": v1_api._now(),
        "render_status": "ready", "playback": "stopped", "progress": 100,
        "message": "十商品轮播验收", "queue": queue, "queue_index": 0, "sequence": 0,
        "loop_count": 0, "auto_rotate": True, "scene_template": "3c", "orientation": "landscape",
        "expression_mode": "normal", "item_elapsed_seconds": 0, "item_started_epoch": None,
        "item_deadline_epoch": None, "interactions": [],
    }
    v1_api._write_session = lambda _session: None
    with v1_api._sessions_lock:
        v1_api._sessions.clear()
        v1_api._sessions[test_id] = session
    threading.Thread(target=v1_api._scheduler_loop, name="ten-product-test-scheduler", daemon=True).start()

    started = v1_api.control_live(test_id, "start")
    assert started["playback"] == "playing"
    observed = [0]
    deadline = time.time() + 175
    while time.time() < deadline:
        with v1_api._sessions_lock:
            state = copy.deepcopy(v1_api._sessions[test_id])
        index = int(state["queue_index"])
        if index != observed[-1]:
            observed.append(index)
        if int(state["sequence"]) >= 10:
            break
        time.sleep(0.25)
    assert state["sequence"] >= 10, state
    assert observed[:11] == list(range(10)) + [0], observed
    assert state["loop_count"] >= 1

    switched = v1_api.switch_product(test_id, v1_api.ProductSwitchRequest(index=5))
    assert switched["queue_index"] == 5 and switched["active_product"]["name"] == names[5]
    stopped = v1_api.control_live(test_id, "stop")
    assert stopped["playback"] == "stopped"
    print(json.dumps({
        "ok": True, "distinct_products": len(names), "observed_queue_indexes": observed,
        "automatic_rotations": state["sequence"], "loops": state["loop_count"],
        "manual_switch": {"index": 5, "product": names[5]}, "controls": ["start", "switch", "stop"],
        "per_product_seconds": 15,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
