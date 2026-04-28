from __future__ import annotations

from typing import Any


CORE_LABEL_KEYS = (
    "high_frequency_region",
    "high_daily_region_profit",
    "regional_high_win_rate",
    "lottery_player",
    "split_player",
    "liquidity_player",
)

CORE_LABEL_DEFAULT_RULES: dict[str, dict[str, Any]] = {
    "high_frequency_region": {
        "key": "high_frequency_region",
        "display_name": "高频地区：{dominant_region}",
        "description": "天气交易主要集中在单一地区。",
        "all": [{"field": "dominant_region_trade_ratio", "op": ">=", "value": 0.4}],
    },
    "high_daily_region_profit": {
        "key": "high_daily_region_profit",
        "display_name": "高暴击：{max_region_daily_profit_region}",
        "description": "同城同日卖出金额相对买入金额出现高倍数。",
        "all": [{"field": "max_region_daily_profit_multiple", "op": ">", "value": 2}],
    },
    "regional_high_win_rate": {
        "key": "regional_high_win_rate",
        "display_name": "高胜率：{best_region_win_rate_region}",
        "description": "某城市正收益交易日占比较高。",
        "all": [
            {
                "field": "best_region_positive_return_day_ratio",
                "op": ">=",
                "value": 0.6,
            },
            {
                "field": "best_region_trade_count",
                "op": ">=",
                "value": 3,
            }
        ],
    },
    "lottery_player": {
        "key": "lottery_player",
        "display_name": "彩票型",
        "description": "低筹码成本交易占比较高。",
        "all": [{"field": "low_chip_cost_trade_ratio", "op": ">", "value": 0.5}],
    },
    "split_player": {
        "key": "split_player",
        "display_name": "拆分型",
        "description": "持仓筹码成本与链上证据共同支持拆分行为。",
        "all": [{"field": "split_player_validation_passed", "op": "==", "value": True}],
    },
    "liquidity_player": {
        "key": "liquidity_player",
        "display_name": "流动型",
        "description": "低 swap 活动与卖出主导地区日共同支持流动型行为。",
        "all": [{"field": "liquidity_player_matched", "op": "==", "value": True}],
    },
}


def evaluate_labels(
    metrics: dict[str, Any],
    label_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for rule in label_rules:
        if rule.get("enabled", True) is False:
            continue
        if rule_matches(metrics, rule):
            key = render_label_template(rule.get("key"), metrics)
            matched.append(
                {
                    "key": key,
                    "display_name": render_label_template(rule.get("display_name"), metrics),
                    "description": render_label_template(rule.get("description"), metrics),
                    "evidence": build_label_evidence(str(key), metrics, rule),
                }
            )
    return matched


def evaluate_label_evaluations(
    metrics: dict[str, Any],
    label_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return complete positive/negative evaluations for the six core labels."""
    configured_rules = enabled_core_rules(label_rules)
    evaluations: list[dict[str, Any]] = []
    for key in CORE_LABEL_KEYS:
        rule = merged_core_rule(key, configured_rules.get(key))
        matched = rule_matches(metrics, rule)
        evidence = build_label_evidence(key, metrics, rule, matched=matched)
        facts = evidence["details"]
        reason = normalized_core_reason(key, facts, matched, str(evidence["reason"]))
        evaluations.append(
            {
                "key": key,
                "display_name": render_label_template(rule.get("display_name"), metrics),
                "description": render_label_template(rule.get("description"), metrics),
                "matched": matched,
                "reason": reason,
                "facts": facts,
                "records": build_label_records(key, facts, matched),
                "details": facts,
            }
        )
    return evaluations


def enabled_core_rules(label_rules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rules: dict[str, dict[str, Any]] = {}
    for rule in label_rules:
        if rule.get("enabled", True) is False:
            continue
        key = str(rule.get("key", ""))
        if key in CORE_LABEL_KEYS and key not in rules:
            rules[key] = rule
    return rules


def merged_core_rule(key: str, configured_rule: dict[str, Any] | None) -> dict[str, Any]:
    default_rule = CORE_LABEL_DEFAULT_RULES[key]
    if configured_rule is None:
        return dict(default_rule)

    merged = dict(default_rule)
    merged.update(configured_rule)
    merged["key"] = key
    if key == "regional_high_win_rate":
        merged["all"] = ensure_condition(
            list(merged.get("all") or []),
            {"field": "best_region_trade_count", "op": ">=", "value": 3},
        )
    return merged


def build_label_evidence(
    key: str,
    metrics: dict[str, Any],
    rule: dict[str, Any],
    *,
    matched: bool = True,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "conditions": build_condition_evaluations(metrics, rule),
    }
    reason = "已满足该标签的全部判定条件。"

    if key == "high_frequency_region":
        regional_trade_summary = metrics.get("regional_trade_summary", {}) or {}
        numerator = int(
            metrics.get("dominant_region_trade_count")
            or regional_trade_summary.get("dominant_region_trade_count")
            or 0
        )
        denominator = int(
            regional_trade_summary.get("total_count")
            or metrics.get("trade_count")
            or 0
        )
        count_mode = str(
            regional_trade_summary.get("count_mode")
            or ("region_day" if regional_trade_summary.get("region_day_count") else "trade")
        )
        raw_trade_count = int(
            regional_trade_summary.get("dominant_region_raw_trade_count")
            or numerator
        )
        details.update(
            {
                "region": metrics.get("dominant_region") or "",
                "city": metrics.get("dominant_region") or "",
                "ratio": metrics.get("dominant_region_trade_ratio") or 0.0,
                "threshold": first_condition_value(rule),
                "numerator": numerator,
                "denominator": denominator,
                "count_mode": count_mode,
                "raw_trade_count": raw_trade_count,
                "regions": regional_trade_summary.get("regions", []),
            }
        )
        if count_mode == "region_day":
            reason = (
                f"{details['region'] or '未识别地区'} 地区-日期样本 {numerator}/{denominator}，"
                f"占比 {float(details['ratio']):.2%}，"
                f"对应原始天气交易 {raw_trade_count} 笔。"
            )
        else:
            reason = (
                f"{details['region'] or '未识别地区'} 交易 {numerator}/{denominator} 笔，"
                f"占比 {float(details['ratio']):.2%}。"
            )
    elif key == "high_daily_region_profit":
        buy_amount = metrics.get("max_region_daily_profit_buy_amount") or 0.0
        sell_amount = metrics.get("max_region_daily_profit_sell_amount") or 0.0
        details.update(
            {
                "region": metrics.get("max_region_daily_profit_region") or "",
                "city": metrics.get("max_region_daily_profit_region") or "",
                "date": metrics.get("max_region_daily_profit_date") or "",
                "multiple": metrics.get("max_region_daily_profit_multiple") or 0.0,
                "threshold": first_condition_value(rule),
                "buy_amount": buy_amount,
                "sell_amount": sell_amount,
                "buy": buy_amount,
                "sell": sell_amount,
                "numerator": sell_amount,
                "denominator": buy_amount,
                "top_region_days": metrics.get("regional_daily_profit_summary", {}).get(
                    "qualified_region_days",
                    [],
                )[:10],
                "region_days": metrics.get("regional_daily_profit_summary", {}).get(
                    "region_days",
                    [],
                )[:10],
            }
        )
        reason = (
            f"{details['date'] or '-'} {details['region'] or '未识别地区'} "
            f"卖出 {sell_amount:.2f}、买入 {buy_amount:.2f}，整体盈利倍数 {float(details['multiple']):.2f}x。"
        )
    elif key == "regional_high_win_rate":
        numerator = int(metrics.get("best_region_positive_return_days") or 0)
        denominator = int(metrics.get("best_region_total_trade_days") or 0)
        trade_count = int(metrics.get("best_region_trade_count") or 0)
        details.update(
            {
                "region": metrics.get("best_region_win_rate_region") or "",
                "city": metrics.get("best_region_win_rate_region") or "",
                "ratio": metrics.get("best_region_positive_return_day_ratio") or 0.0,
                "threshold": condition_value(rule, "best_region_positive_return_day_ratio")
                or first_condition_value(rule),
                "trade_count": trade_count,
                "min_trade_count": condition_value(rule, "best_region_trade_count") or 0,
                "numerator": numerator,
                "denominator": denominator,
                "regions": metrics.get("regional_day_win_rate_summary", {}).get("regions", []),
            }
        )
        reason = (
            f"{details['region'] or '未识别地区'} 正收益天数 {numerator}/{denominator}，"
            f"占比 {float(details['ratio']):.2%}。"
        )
    elif key == "lottery_player":
        numerator = int(metrics.get("low_chip_cost_trade_count") or 0)
        denominator = int(metrics.get("trade_count") or 0)
        summary = metrics.get("low_chip_cost_summary", {})
        details.update(
            {
                "ratio": metrics.get("low_chip_cost_trade_ratio") or 0.0,
                "ratio_threshold": first_condition_value(rule),
                "numerator": numerator,
                "denominator": denominator,
                "threshold": metrics.get("low_chip_cost_threshold")
                or summary.get("threshold")
                or 30.0,
                "top_low_chip_region": metrics.get("top_low_chip_region")
                or summary.get("top_low_chip_region")
                or "",
                "top_low_chip_region_count": metrics.get("top_low_chip_region_count")
                or summary.get("top_low_chip_region_count")
                or 0,
                "top_low_chip_region_ratio": metrics.get("top_low_chip_region_ratio")
                or summary.get("top_low_chip_region_ratio")
                or 0.0,
                "regions": summary.get("low_chip_regions", []),
                "top_low_chip_records": summary.get("low_chip_records", []),
            }
        )
        reason = (
            f"低成本交易 {numerator}/{denominator} 笔，占比 {float(details['ratio']):.2%}；"
            f"最集中地区为 {details['top_low_chip_region'] or '-'}。"
        )
    elif key == "split_player":
        details.update(
            {
                "average_chip_cost": metrics.get("split_avg_chip_cost") or 0.0,
                "target_chip_cost": metrics.get("split_avg_chip_cost_target") or 0.0,
                "tolerance": metrics.get("split_avg_chip_cost_tolerance") or 0.0,
                "average_chip_cost_matched": metrics.get("split_avg_chip_cost_matched")
                or False,
                "split_chain_verified": metrics.get("split_chain_verified") or False,
                "chain_validation_status": metrics.get("chain_validation_status") or "",
                "chain_validation_reason": metrics.get("chain_validation_reason") or "",
                "numerator": metrics.get("split_evidence_count") or 0,
                "denominator": metrics.get("required_split_evidence_count") or 0,
                "chain_evidence": metrics.get("chain_validation", {}).get("evidence", [])[:10],
            }
        )
        reason = (
            f"持仓均价 {float(details['average_chip_cost']):.2f}，"
            f"链上证据 {details['numerator']}/{details['denominator']} 条。"
        )
    elif key == "liquidity_player":
        summary = metrics.get("liquidity_player_summary", {})
        top_days = summary.get("sell_dominant_region_days", [])
        top_day = top_days[0] if top_days else {}
        numerator = int(summary.get("sell_dominant_day_count") or 0)
        denominator = int(summary.get("unique_trade_day_count") or 0)
        details.update(
            {
                "swap_ratio": metrics.get("liquidity_swap_ratio") or 0.0,
                "swap_ratio_threshold": summary.get("swap_ratio_threshold", 0.1),
                "swap_count": metrics.get("liquidity_swap_count") or 0,
                "low_swap_activity": metrics.get("liquidity_low_swap_activity") or False,
                "ratio": (numerator / denominator if denominator else 0.0),
                "sell_dominant_ratio_threshold": 0.5,
                "numerator": numerator,
                "denominator": denominator,
                "region": top_day.get(
                    "region",
                    metrics.get("liquidity_top_sell_dominant_region", ""),
                ),
                "city": top_day.get(
                    "region",
                    metrics.get("liquidity_top_sell_dominant_region", ""),
                ),
                "date": top_day.get("date", metrics.get("liquidity_top_sell_dominant_date", "")),
                "sell": top_day.get("sell_trade_count", 0),
                "buy": max(
                    0,
                    int(top_day.get("trade_count", 0) or 0)
                    - int(top_day.get("sell_trade_count", 0) or 0),
                ),
                "top_sell_dominant_region_days": top_days[:10],
                "region_days": summary.get("region_days", [])[:10],
            }
        )
        reason = (
            f"swap 占比 {float(details['swap_ratio']):.2%}；卖出主导交易日 {numerator}/{denominator}，"
            f"占比 {float(details['ratio']):.2%}。"
        )
    elif key in {"normal_active", "low_active"}:
        details.update(
            {
                "current_date": metrics.get("current_date") or "",
                "latest_trade_date": metrics.get("latest_trade_date") or "",
                "days_since_latest_trade": metrics.get("days_since_latest_trade") or 0,
                "activity_level": metrics.get("activity_level") or "",
                "numerator": metrics.get("days_since_latest_trade") or 0,
                "denominator": metrics.get("recent_activity_summary", {}).get("active_days", 0),
            }
        )
        reason = (
            f"最近交易日为 {details['latest_trade_date'] or '-'}，距当前日期 "
            f"{details['days_since_latest_trade']} 天。"
        )
    elif key in {"new_wallet", "hidden_expert_new_wallet"}:
        details.update(
            {
                "registration_date": metrics.get("wallet_registration_date") or "",
                "registration_datetime": metrics.get("wallet_registration_datetime") or "",
                "source": metrics.get("wallet_registration_source") or "",
                "wallet_age_days": metrics.get("wallet_age_days") or 0,
                "new_wallet_days": metrics.get("new_wallet_days") or 0,
                "hidden_new_wallet_days": metrics.get("hidden_new_wallet_days") or 0,
                "numerator": metrics.get("wallet_age_days") or 0,
                "denominator": (
                    metrics.get("hidden_new_wallet_days")
                    if key == "hidden_expert_new_wallet"
                    else metrics.get("new_wallet_days")
                )
                or 0,
            }
        )
        reason = (
            f"钱包年龄 {details['wallet_age_days']} 天，注册/首见日期 "
            f"{details['registration_date'] or '-'}（来源：{details['source'] or '-'}）。"
        )
    elif key == "early_positioning":
        numerator = int(metrics.get("high_temp_off_day_buy_count") or 0)
        denominator = int(metrics.get("high_temp_analyzed_buy_count") or 0)
        summary = metrics.get("high_temperature_early_entry_summary", {})
        details.update(
            {
                "ratio": metrics.get("high_temp_off_day_buy_ratio") or 0.0,
                "numerator": numerator,
                "denominator": denominator,
                "same_day_buy_count": metrics.get("high_temp_same_day_buy_count") or 0,
                "missing_market_date_count": metrics.get(
                    "high_temp_missing_market_date_count",
                )
                or 0,
                "top_off_day_records": summary.get("top_off_day_buy_records", [])
                or summary.get("off_day_buy_records", [])[:10],
            }
        )
        first_record = (
            details["top_off_day_records"][0] if details["top_off_day_records"] else {}
        )
        details["region"] = first_record.get("region", "")
        details["city"] = first_record.get("region", "")
        details["date"] = first_record.get("high_temperature_date", "")
        reason = (
            f"非当日高温买入 {numerator}/{denominator} 笔，占比 {float(details['ratio']):.2%}。"
        )
    else:
        details.update(default_metric_details(metrics))

    if not matched:
        reason = build_unmatched_reason(key, details)

    return {
        "matched": matched,
        "reason": reason,
        "details": details,
    }


def build_condition_evaluations(
    metrics: dict[str, Any],
    rule: dict[str, Any],
) -> list[dict[str, Any]]:
    evaluations: list[dict[str, Any]] = []
    for group in ("all", "any"):
        for condition in list(rule.get(group) or []):
            field = str(condition.get("field", ""))
            evaluations.append(
                {
                    "group": group,
                    "field": field,
                    "op": condition.get("op", "=="),
                    "value": condition.get("value"),
                    "actual": metrics.get(field),
                    "matched": condition_matches(metrics, condition),
                }
            )
    return evaluations


def first_condition_value(rule: dict[str, Any]) -> Any:
    for condition in [*list(rule.get("all") or []), *list(rule.get("any") or [])]:
        return condition.get("value")
    return None


def condition_value(rule: dict[str, Any], field_name: str) -> Any:
    for condition in [*list(rule.get("all") or []), *list(rule.get("any") or [])]:
        if str(condition.get("field", "")) == field_name:
            return condition.get("value")
    return None


def ensure_condition(
    conditions: list[dict[str, Any]],
    required: dict[str, Any],
) -> list[dict[str, Any]]:
    required_field = str(required.get("field", ""))
    if any(str(condition.get("field", "")) == required_field for condition in conditions):
        return conditions
    return [*conditions, required]


def normalized_core_reason(
    key: str,
    details: dict[str, Any],
    matched: bool,
    fallback: str,
) -> str:
    if key == "high_frequency_region":
        region = str(details.get("region") or "\u672a\u8bc6\u522b\u5730\u533a")
        grouped_count = int(details.get("numerator") or 0)
        total_grouped_count = int(details.get("denominator") or 0)
        raw_trade_count = int(details.get("raw_trade_count") or 0)
        ratio = float(details.get("ratio") or 0.0)
        count_mode = str(details.get("count_mode") or "trade")
        tail = (
            "\u5df2\u6ee1\u8db3\u9ad8\u9891\u5730\u533a\u6807\u7b7e\u3002"
            if matched
            else "\u672a\u8fbe\u5230\u9ad8\u9891\u5730\u533a\u6807\u7b7e\u9608\u503c\u3002"
        )
        if count_mode != "region_day":
            return (
                f"{region} \u4ea4\u6613 {grouped_count}/{total_grouped_count} \u7b14\uff0c"
                f"\u5360\u6bd4 {ratio:.2%}\uff0c{tail}"
            )
        return (
            f"{region} \u5730\u533a-\u65e5\u671f\u6837\u672c {grouped_count}/{total_grouped_count}\uff0c"
            f"\u5360\u6bd4 {ratio:.2%}\uff0c"
            f"\u5bf9\u5e94\u539f\u59cb\u5929\u6c14\u4ea4\u6613 {raw_trade_count} \u7b14\uff0c"
            f"{tail}"
        )

    if key != "regional_high_win_rate":
        return fallback

    region = str(details.get("region") or "\u672a\u8bc6\u522b\u5730\u533a")
    positive_days = int(details.get("numerator") or 0)
    total_days = int(details.get("denominator") or 0)
    trade_count = int(details.get("trade_count") or 0)
    min_trade_count = int(details.get("min_trade_count") or 0)
    ratio = float(details.get("ratio") or 0.0)
    threshold_text = (
        f"\uff08\u9608\u503c >= {min_trade_count}\uff09" if min_trade_count > 0 else ""
    )
    tail = (
        "\u5df2\u6ee1\u8db3\u9ad8\u80dc\u7387\u6807\u7b7e\u3002"
        if matched
        else "\u672a\u8fbe\u5230\u9ad8\u80dc\u7387\u6807\u7b7e\u9608\u503c\u3002"
    )
    return (
        f"{region} \u6b63\u6536\u76ca\u5929\u6570 {positive_days}/{total_days}\uff0c"
        f"\u5730\u533a\u4ea4\u6613 {trade_count} \u7b14{threshold_text}\uff0c"
        f"\u5360\u6bd4 {ratio:.2%}\uff0c{tail}"
    )


def build_unmatched_reason(key: str, details: dict[str, Any]) -> str:
    if key == "high_frequency_region":
        count_mode = str(details.get("count_mode") or "trade")
        if count_mode == "region_day":
            return (
                f"{details.get('region') or '未识别主地区'} 地区-日期样本 "
                f"{int(details.get('numerator') or 0)}/{int(details.get('denominator') or 0)}，"
                f"占比 {float(details.get('ratio') or 0):.2%}，未达到核心阈值。"
            )
        return (
            f"{details.get('region') or '未识别主地区'} 交易 "
            f"{int(details.get('numerator') or 0)}/{int(details.get('denominator') or 0)} 笔，"
            f"占比 {float(details.get('ratio') or 0):.2%}，未达到核心阈值。"
        )
    if key == "high_daily_region_profit":
        return (
            f"最佳地区日 {details.get('date') or '-'} {details.get('region') or '-'} "
            f"整体盈利倍数仅 {float(details.get('multiple') or 0):.2f}x，未达到高暴击阈值。"
        )
    if key == "regional_high_win_rate":
        return (
            f"{details.get('region') or '未识别地区'} 正收益天数 "
            f"{int(details.get('numerator') or 0)}/{int(details.get('denominator') or 0)}，"
            f"占比 {float(details.get('ratio') or 0):.2%}，未达到高胜率阈值。"
        )
    if key == "lottery_player":
        return (
            f"低成本交易 {int(details.get('numerator') or 0)}/"
            f"{int(details.get('denominator') or 0)} 笔，"
            f"占比 {float(details.get('ratio') or 0):.2%}，未达到彩票型阈值。"
        )
    if key == "split_player":
        return (
            f"持仓均价 {float(details.get('average_chip_cost') or 0):.2f}；"
            f"链上状态 {details.get('chain_validation_status') or '-'}，"
            f"证据 {int(details.get('numerator') or 0)}/"
            f"{int(details.get('denominator') or 0)} 条。"
        )
    if key == "liquidity_player":
        return (
            f"swap 占比 {float(details.get('swap_ratio') or 0):.2%}；"
            f"卖出主导交易日 {int(details.get('numerator') or 0)}/"
            f"{int(details.get('denominator') or 0)}，"
            f"占比 {float(details.get('ratio') or 0):.2%}，未达到流动型阈值。"
        )
    failed = [condition for condition in details.get("conditions", []) if not condition.get("matched")]
    if failed:
        condition = failed[0]
        return (
            f"{condition.get('field')} {condition.get('op')} {condition.get('value')} 未满足；"
            f"实际值为 {condition.get('actual')}。"
        )
    return "未满足该核心标签的全部条件。"


def build_label_records(
    key: str,
    facts: dict[str, Any],
    matched: bool,
) -> list[dict[str, Any]]:
    record_type = "evidence" if matched else "counterevidence"
    if key == "high_frequency_region":
        records = [
            {"type": record_type, "source": "regional_trade_summary.regions", **record}
            for record in facts.get("regions", [])[:10]
        ]
    elif key == "high_daily_region_profit":
        source_records = facts.get("top_region_days") or facts.get("region_days") or []
        records = [
            {"type": record_type, "source": "regional_daily_profit_summary.region_days", **record}
            for record in source_records[:10]
        ]
    elif key == "regional_high_win_rate":
        records = [
            {"type": record_type, "source": "regional_day_win_rate_summary.regions", **record}
            for record in facts.get("regions", [])[:10]
        ]
    elif key == "lottery_player":
        source_records = facts.get("top_low_chip_records") or facts.get("regions") or []
        records = [
            {"type": record_type, "source": "low_chip_cost_summary", **record}
            for record in source_records[:10]
        ]
    elif key == "split_player":
        records = [
            {"type": record_type, "source": "chain_validation.evidence", **record}
            for record in facts.get("chain_evidence", [])[:10]
        ]
    elif key == "liquidity_player":
        source_records = facts.get("top_sell_dominant_region_days") or facts.get("region_days") or []
        records = [
            {"type": record_type, "source": "liquidity_player_summary.region_days", **record}
            for record in source_records[:10]
        ]
    else:
        records = []

    if records:
        return records

    return [
        {
            "type": record_type,
            "source": "metrics",
            "numerator": facts.get("numerator", 0),
            "denominator": facts.get("denominator", 0),
            "ratio": facts.get("ratio", 0.0),
            "region": facts.get("region", ""),
            "city": facts.get("city", ""),
        }
    ]


def build_strategy_notes(
    metrics: dict[str, Any],
    labels: list[dict[str, Any]],
) -> list[str]:
    notes: list[str] = []
    label_names = {str(label.get("key")) for label in labels}

    if "weather_specialist" in label_names:
        notes.append("交易明显集中在天气赛道，资金与交易频次都呈现出专题化特征。")
    if "cross_market_allocator" in label_names:
        notes.append("事件分散度较高，更像做跨市场配置，而不是压单一事件。")
    if "high_frequency_operator" in label_names:
        notes.append("活跃天内下单密度较高，偏向高频/短线执行。")
    if "swing_trader" in label_names:
        notes.append("存在跨天到跨周的持有行为，偏波段而非超短。")
    if "resolution_sniper" in label_names:
        notes.append("不少交易发生在临近结算阶段，像是在吃 resolution 前后的信息差。")
    if "high_conviction" in label_names:
        notes.append("单事件暴露较高，仓位表达更集中，存在重仓押注倾向。")
    if "high_win_rate" in label_names:
        notes.append("已平仓头寸的胜率较高，兑现能力相对突出。")
    if "liquidity_reward_farmer" in label_names:
        notes.append("奖励活动占比较显著，策略中带有做市/挖激励的成分。")
    if "long_horizon_holder" in label_names:
        notes.append("当前仓位中长周期到期市场占比较高，说明有长线布局。")

    if "high_frequency_region" in label_names and metrics.get("dominant_region"):
        notes.append(
            f"\u4ea4\u6613\u9891\u6b21\u660e\u663e\u96c6\u4e2d\u5728 {metrics['dominant_region']} \u5730\u533a\uff0c\u5df2\u89e6\u53d1\u9ad8\u9891\u4ea4\u6613\u5730\u533a\u6807\u7b7e\u3002"
        )
    if "high_daily_region_profit" in label_names and metrics.get("max_region_daily_profit_region"):
        notes.append(
            f"{metrics['max_region_daily_profit_region']} \u5728 {metrics.get('max_region_daily_profit_date') or '-'} \u7684\u540c\u5730\u533a\u5355\u65e5\u6574\u4f53\u76c8\u5229\u500d\u6570\u8d85\u8fc7 2x\uff0c\u5df2\u89e6\u53d1\u9ad8\u66b4\u51fb\u6807\u7b7e\u3002"
        )
    if "regional_high_win_rate" in label_names and metrics.get("best_region_win_rate_region"):
        notes.append(
            f"{metrics['best_region_win_rate_region']} \u5730\u533a\u6b63\u6536\u76ca\u5929\u6570\u5360\u6bd4\u8fbe\u5230 {float(metrics.get('best_region_positive_return_day_ratio') or 0):.2%}\uff0c\u5df2\u89e6\u53d1\u9ad8\u80dc\u7387\u6807\u7b7e\u3002"
        )
    if "lottery_player" in label_names:
        notes.append(
            f"\u7b79\u7801\u6210\u672c\u4f4e\u4e8e 30 \u7684\u4ea4\u6613\u5360\u6bd4\u8fbe\u5230 {float(metrics.get('low_chip_cost_trade_ratio') or 0):.2%}\uff0c\u5df2\u89e6\u53d1\u5f69\u7968\u578b\u9009\u624b\u6807\u7b7e\u3002"
        )
    if "split_player" in label_names:
        notes.append(
            f"\u6301\u4ed3\u5747\u4ef7\u7b79\u7801\u63a5\u8fd1 {float(metrics.get('split_avg_chip_cost_target') or 5):.2f}\uff0c\u4e14\u94fe\u4e0a\u5df2\u9a8c\u8bc1 {int(metrics.get('split_evidence_count') or 0)} \u7b14 Neg Risk Adapter convertPositions \u8bb0\u5f55\uff0c\u5df2\u89e6\u53d1\u62c6\u5206\u578b\u9009\u624b\u6807\u7b7e\u3002"
        )
    if "liquidity_player" in label_names:
        notes.append(
            f"swap \u5360\u6bd4 {float(metrics.get('liquidity_swap_ratio') or 0):.2%}\uff0c\u5356\u51fa\u4e3b\u5bfc\u5730\u533a\u65e5\u5360\u6bd4 {float(metrics.get('liquidity_sell_dominant_region_day_ratio') or 0):.2%}\uff0c\u5df2\u89e6\u53d1\u6d41\u52a8\u578b\u9009\u624b\u6807\u7b7e\u3002"
        )
    if "normal_active" in label_names or "low_active" in label_names:
        level_label = (
            "\u6b63\u5e38\u6d3b\u8dc3"
            if "normal_active" in label_names
            else "\u4f4e\u6d3b\u8dc3"
        )
        notes.append(
            f"\u6700\u65b0\u4ea4\u6613\u65e5\u671f\u4e3a {metrics.get('latest_trade_date') or '-'}\uff0c\u8ddd\u5f53\u524d\u65e5\u671f {int(metrics.get('days_since_latest_trade') or 0)} \u5929\uff0c\u6d3b\u8dc3\u72b6\u6001\uff1a{level_label}\u3002"
        )
    if "new_wallet" in label_names or "hidden_expert_new_wallet" in label_names:
        wallet_label = (
            "\u9690\u85cf\u9ad8\u624b\u65b0\u94b1\u5305"
            if "hidden_expert_new_wallet" in label_names
            else "\u65b0\u94b1\u5305"
        )
        notes.append(
            f"\u94b1\u5305\u6ce8\u518c/\u9996\u6b21\u53ef\u9a8c\u8bc1\u65e5\u671f\u4e3a {metrics.get('wallet_registration_date') or '-'}\uff0c\u8ddd\u5f53\u524d\u65e5\u671f {int(metrics.get('wallet_age_days') or 0)} \u5929\uff0c\u6570\u636e\u6765\u6e90\uff1a{metrics.get('wallet_registration_source') or '-'}\uff0c\u5df2\u89e6\u53d1{wallet_label}\u6807\u7b7e\u3002"
        )
    if "early_positioning" in label_names:
        notes.append(
            f"\u6700\u9ad8\u6e29\u5e02\u573a BUY \u8bb0\u5f55\u4e2d\uff0c\u4e70\u5165\u65e5\u4e0e\u6700\u9ad8\u6e29\u5f53\u65e5\u4e0d\u540c\u65e5\u5360\u6bd4 {float(metrics.get('high_temp_off_day_buy_ratio') or 0):.2%}\uff0c\u5df2\u89e6\u53d1\u63d0\u524d\u57cb\u4f0f\u6807\u7b7e\u3002"
        )

    if not notes:
        if metrics.get("trade_count", 0) == 0:
            notes.append("公开 activity 中几乎没有可用于分析的交易记录。")
        elif metrics.get("weather_notional_ratio", 0) >= 0.35:
            notes.append("虽然未触发强标签，但天气相关仓位占比已经不低。")
        else:
            notes.append("当前更像泛赛道交易者，天气赛道并非绝对主战场。")
    return notes


def default_metric_details(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_count": metrics.get("trade_count") or 0,
        "buy": metrics.get("buy_trade_count") or 0,
        "sell": metrics.get("sell_trade_count") or 0,
        "weather_trade_count": metrics.get("weather_trade_count") or 0,
        "weather_trade_ratio": metrics.get("weather_trade_ratio") or 0.0,
        "weather_notional_ratio": metrics.get("weather_notional_ratio") or 0.0,
        "closed_position_win_rate": metrics.get("closed_position_win_rate") or 0.0,
        "closed_profit_multiple": metrics.get("closed_profit_multiple") or 0.0,
        "trades_per_active_day": metrics.get("trades_per_active_day") or 0.0,
        "median_trade_notional": metrics.get("median_trade_notional") or 0.0,
        "largest_event_notional_ratio": metrics.get("largest_event_notional_ratio") or 0.0,
        "reward_activity_count": metrics.get("reward_activity_count") or 0,
        "reward_total_usdc": metrics.get("reward_total_usdc") or 0.0,
        "open_position_long_dated_ratio": metrics.get("open_position_long_dated_ratio") or 0.0,
        "numerator": metrics.get("weather_trade_count") or metrics.get("trade_count") or 0,
        "denominator": metrics.get("trade_count") or 0,
    }


def render_label_template(value: Any, metrics: dict[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    if "{" not in value:
        return value
    try:
        return value.format_map(_MetricTemplateValues(metrics))
    except (KeyError, ValueError):
        return value


class _MetricTemplateValues:
    def __init__(self, metrics: dict[str, Any]) -> None:
        self.metrics = metrics

    def __getitem__(self, key: str) -> Any:
        value: Any = self.metrics
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return ""
            value = value[part]
        if isinstance(value, (dict, list, tuple, set)):
            return str(value)
        return "" if value is None else value


def rule_matches(metrics: dict[str, Any], rule: dict[str, Any]) -> bool:
    all_conditions = rule.get("all", [])
    any_conditions = rule.get("any", [])

    all_ok = all(condition_matches(metrics, condition) for condition in all_conditions)
    any_ok = True if not any_conditions else any(
        condition_matches(metrics, condition) for condition in any_conditions
    )
    return all_ok and any_ok


def condition_matches(metrics: dict[str, Any], condition: dict[str, Any]) -> bool:
    field = str(condition.get("field", ""))
    op = str(condition.get("op", "=="))
    target = condition.get("value")
    actual = metrics.get(field, 0)

    try:
        actual_value = float(actual)
        target_value = float(target)
    except (TypeError, ValueError):
        actual_value = actual
        target_value = target

    if op == ">":
        return actual_value > target_value
    if op == ">=":
        return actual_value >= target_value
    if op == "<":
        return actual_value < target_value
    if op == "<=":
        return actual_value <= target_value
    if op == "!=":
        return actual_value != target_value
    return actual_value == target_value
