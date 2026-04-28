from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .labels import CORE_LABEL_DEFAULT_RULES, CORE_LABEL_KEYS


UTC = timezone.utc

CORE_LABEL_NAMES = {
    "high_frequency_region": "高频地区",
    "high_daily_region_profit": "高暴击",
    "regional_high_win_rate": "高胜率",
    "lottery_player": "彩票型",
    "split_player": "拆分型",
    "liquidity_player": "流动型",
}

LABEL_RULE_DEFINITIONS = (
    {
        "label": "高频交易地区",
        "rule": "按地址全部交易记录的地区字段统计交易次数；某地区交易次数 ÷ 总交易次数 > 60% 时命中。若所有地区占比均 <= 60% 且地区占比差距 <= 10%，不命中。",
        "source": "本地交易地区字段、地区交易次数、总交易次数",
    },
    {
        "label": "高暴击",
        "rule": "按同一地区同一天所有温度区间汇总总买入金额与总卖出金额；整体盈利倍数 = 总卖出金额 ÷ 总买入金额，> 2x 时命中。",
        "source": "地区、交易日期、总买入金额、总卖出金额、整体盈利倍数",
    },
    {
        "label": "高胜率",
        "rule": "按地区统计交易日；正收益天数 ÷ 总交易天数 >= 60% 时命中。",
        "source": "地区、交易日期、当日买入/卖出金额、正收益天数、总交易天数",
    },
    {
        "label": "彩票型选手",
        "rule": "筹码成本 < 30 的交易次数 ÷ 总交易次数 > 50% 时命中。",
        "source": "筹码成本字段、低成本交易次数、总交易次数",
    },
    {
        "label": "拆分型选手",
        "rule": "持仓均价接近 5，且 Polymarket 链上记录明确验证存在拆分操作时命中；无明确链上拆分记录或任一条件不满足均不命中。",
        "source": "持仓均价、Neg Risk Adapter convertPositions 链上记录、拆分证据数",
    },
    {
        "label": "流动型选手",
        "rule": "Polymarket 特定 swap 次数占总交易次数 < 10% 或 swap 次数 = 0，且卖出主导日期数 ÷ 总交易日期数 > 50% 时命中。卖出主导日指当日该地区已卖出记录占该日该地区总交易次数 > 50%。",
        "source": "链上/活动 swap 记录、总交易次数、地区、日期、已卖出记录、总交易日期数",
    },
    {
        "label": "活跃",
        "rule": "最新交易日期距当前日期 <= 3 天时命中活跃；其中 <= 1 天为正常活跃，2-3 天为低活跃。",
        "source": "最新交易日期、当前日期、活跃窗口",
    },
    {
        "label": "新钱包",
        "rule": "地址注册日期或首笔可验证链上交易日期距当前日期 < 2 个月时命中新钱包；< 10 天时命中隐藏高手新钱包。",
        "source": "注册地址、首笔链上交易日期、当前日期、钱包年龄",
    },
    {
        "label": "提前埋伏",
        "rule": "仅统计某日最高温气温市场交易记录；买入日期与对应最高温当日不同日的交易记录占比 > 50% 时命中。",
        "source": "最高温市场记录、买入日期、最高温日期、不同日买入占比",
    },
)


def build_report(
    *,
    config: dict[str, Any],
    leaderboard: list[dict[str, Any]],
    weather_events: list[dict[str, Any]],
    wallet_results: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> str:
    lines = [
        "# Polymarket 天气钱包筛选报告",
        "",
        f"- 生成时间：{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"- 排行榜记录：{len(leaderboard)} 条",
        f"- 天气事件索引：{len(weather_events)} 个",
        f"- 入选钱包：{len(wallet_results)} 个",
        f"- 失败钱包：{len(errors)} 个",
        "",
    ]

    append_filter_conditions(lines, config)
    append_label_rule_definitions(lines, config)

    if errors:
        append_errors(lines, errors)

    if not wallet_results:
        lines.extend(
            [
                "## 关键发现",
                "",
                "本次没有钱包通过筛选条件。建议放宽 PnL、成交量或交易笔数门槛后重新运行。",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    append_label_overview(lines, wallet_results)
    append_key_findings(lines, wallet_results)
    append_wallet_deep_analysis(lines, wallet_results)
    return "\n".join(lines).strip() + "\n"


def append_filter_conditions(lines: list[str], config: dict[str, Any]) -> None:
    wallet_filter = config.get("wallet_filter", {})
    leaderboard = config.get("leaderboard", {})
    weather = config.get("weather", {})
    analysis = config.get("analysis", {})
    chain_validation = config.get("chain_validation", {})

    lines.extend(
        [
            "## 筛选条件",
            "",
            f"- 排行榜范围：category={leaderboard.get('category', '-')}, time_period={leaderboard.get('time_period', '-')}, order_by={leaderboard.get('order_by', '-')}",
            f"- 拉取上限：排行榜 {leaderboard.get('fetch_limit', '-')} 条，天气事件 {weather.get('max_events', '-')} 个",
            f"- 入选目标：{wallet_filter.get('target_count', '-')} 个钱包",
            f"- 最小 PnL：{format_currency(wallet_filter.get('min_pnl'))}",
            f"- 最小成交量：{format_currency(wallet_filter.get('min_volume'))}",
            f"- 最小交易笔数：{format_count(wallet_filter.get('min_traded_count'))}",
            f"- 并发钱包分析：{analysis.get('concurrent_wallets', '-')} 个",
            f"- 链上拆分验证：{'开启' if chain_validation.get('enabled') else '关闭'}",
            "",
        ]
    )


def append_label_rule_definitions(lines: list[str], config: dict[str, Any]) -> None:
    lines.extend(
        [
            "## 标签规则定义",
            "",
            "| 标签 | 判定规则 | 证据来源 |",
            "| --- | --- | --- |",
        ]
    )
    for key, rule in effective_core_rules(config).items():
        label = CORE_LABEL_NAMES.get(key, key)
        rule_text = format_rule_conditions(rule)
        if key == "high_frequency_region":
            rule_text = f"按天气赛道的地区-日期样本统计；{rule_text}"
        lines.append(
            f"| {label} | {rule_text} | {label_source_text(key)} |"
        )
    lines.append("")


def append_errors(lines: list[str], errors: list[dict[str, Any]]) -> None:
    lines.extend(["## 错误记录", ""])
    for item in errors[:10]:
        lines.append(f"- {item.get('wallet', '-')}: {item.get('error', '-')}")
    if len(errors) > 10:
        lines.append(f"- 其余 {len(errors) - 10} 条错误已省略。")
    lines.append("")


def append_label_overview(lines: list[str], wallet_results: list[dict[str, Any]]) -> None:
    lines.extend(
        [
            "## 标签总览表",
            "",
            "| 钱包 | 用户 | 命中标签 | 主地区 | 最高暴击 | 最近证据日 |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for wallet_result in wallet_results:
        selection = wallet_result.get("selection_record", {})
        metrics = wallet_result.get("metrics", {})
        evaluations = label_evaluations(wallet_result)
        matched = [
            CORE_LABEL_NAMES.get(str(item.get("key")), str(item.get("display_name") or item.get("key")))
            for item in evaluations
            if item.get("matched")
        ]
        lines.append(
            "| "
            + " | ".join(
                [
                    short_address(str(wallet_result.get("wallet", ""))),
                    str(selection.get("user_name") or wallet_result.get("leaderboard_entry", {}).get("userName") or "-"),
                    "、".join(matched) if matched else "无",
                    str(selection.get("main_region") or selection.get("dominant_region") or metrics.get("dominant_region") or "-"),
                    format_multiple(selection.get("highest_burst") or metrics.get("max_region_daily_profit_multiple")),
                    str(selection.get("recent_evidence_date") or latest_evidence_date(evaluations, metrics) or "-"),
                ]
            )
            + " |"
        )
    lines.append("")


def append_key_findings(lines: list[str], wallet_results: list[dict[str, Any]]) -> None:
    label_counts: Counter[str] = Counter()
    region_counts: Counter[str] = Counter()
    for wallet_result in wallet_results:
        for item in label_evaluations(wallet_result):
            if item.get("matched"):
                label_counts[CORE_LABEL_NAMES.get(str(item.get("key")), str(item.get("key")))] += 1
        region = (
            wallet_result.get("selection_record", {}).get("main_region")
            or wallet_result.get("metrics", {}).get("dominant_region")
        )
        if region:
            region_counts[str(region)] += 1

    top_burst = max(
        wallet_results,
        key=lambda wallet: number_value(
            wallet.get("selection_record", {}).get("highest_burst")
            or wallet.get("metrics", {}).get("max_region_daily_profit_multiple")
        ),
    )
    top_burst_metrics = top_burst.get("metrics", {})
    top_burst_selection = top_burst.get("selection_record", {})
    top_region = region_counts.most_common(1)[0] if region_counts else ("-", 0)
    top_label = label_counts.most_common(1)[0] if label_counts else ("无", 0)

    lines.extend(
        [
            "## 关键发现",
            "",
            f"- 最常见主地区：{top_region[0]}，覆盖 {top_region[1]} 个入选钱包。",
            f"- 最常见标签：{top_label[0]}，命中 {top_label[1]} 个入选钱包。",
            (
                f"- 最高暴击钱包：{short_address(str(top_burst.get('wallet', '')))}，"
                f"{top_burst_metrics.get('max_region_daily_profit_region') or top_burst_selection.get('highest_burst_region') or '-'} "
                f"{format_multiple(top_burst_selection.get('highest_burst') or top_burst_metrics.get('max_region_daily_profit_multiple'))}。"
            ),
            f"- 平均天气资金占比：{format_percent(mean_metric(wallet_results, 'weather_notional_ratio'))}。",
            "",
        ]
    )


def append_wallet_deep_analysis(lines: list[str], wallet_results: list[dict[str, Any]]) -> None:
    lines.extend(["## 逐钱包深度分析", ""])
    for index, wallet_result in enumerate(wallet_results, start=1):
        wallet = str(wallet_result.get("wallet", ""))
        entry = wallet_result.get("leaderboard_entry", {})
        selection = wallet_result.get("selection_record", {})
        metrics = wallet_result.get("metrics", {})
        profile = wallet_result.get("profile") or metrics.get("profile") or {}
        evaluations = label_evaluations(wallet_result)

        lines.extend(
            [
                f"### {index}. {short_address(wallet)}",
                "",
                f"- 用户：{entry.get('userName') or selection.get('user_name') or '-'}",
                f"- 排名：{entry.get('rank') or selection.get('rank') or '-'}",
                f"- 排行榜 PnL：{format_currency(metrics.get('leaderboard_pnl') or selection.get('pnl'))}",
                f"- 排行榜成交量：{format_currency(metrics.get('leaderboard_volume') or selection.get('volume'))}",
                f"- 天气资金占比：{format_percent(metrics.get('weather_notional_ratio') or selection.get('weather_notional_ratio'))}",
                f"- 主地区：{metrics.get('dominant_region') or selection.get('main_region') or '-'} ({format_percent(metrics.get('dominant_region_trade_ratio') or selection.get('dominant_region_trade_ratio'))})",
                "",
                "#### 证据摘要条",
                "",
            ]
        )
        for item in evaluations:
            status = "[✓]" if item.get("matched") else "[✗]"
            lines.append(
                f"- {status} {CORE_LABEL_NAMES.get(str(item.get('key')), str(item.get('display_name') or item.get('key')))} -> {item.get('reason') or '-'}"
            )
        if not evaluations:
            lines.append("- 暂无后端标签证据。")

        append_region_path_table(lines, profile)
        append_label_evidence_table(lines, evaluations)
        append_audit_block(lines, wallet_result)
        append_profile_block(lines, profile)
        append_record_samples(lines, wallet_result)
        lines.append("")


def append_region_path_table(lines: list[str], profile: Mapping[str, Any]) -> None:
    city_distribution = safe_mapping(profile.get("city_distribution"))
    cities = safe_sequence(city_distribution.get("cities"))
    lines.extend(
        [
            "",
            "#### 地区交易分布",
            "",
            "| 地区 | 交易数 | 买入 | 卖出 | 交易现金流 | 已平仓盈亏 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if not cities:
        lines.append("| - | 0 | - | - | - | - |")
        return
    for city in cities[:8]:
        row = safe_mapping(city)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("city") or row.get("region") or "-"),
                    format_count(row.get("trade_count")),
                    format_currency(row.get("buy_amount")),
                    format_currency(row.get("sell_amount")),
                    format_currency(row.get("net_trade_cashflow")),
                    format_currency(row.get("realized_pnl")),
                ]
            )
            + " |"
        )


def append_label_evidence_table(lines: list[str], evaluations: list[dict[str, Any]]) -> None:
    lines.extend(
        [
            "",
            "#### 标签证据表格",
            "",
            "| 标签 | 结果 | 关键事实 | 代表记录 |",
            "| --- | --- | --- | --- |",
        ]
    )
    if not evaluations:
        lines.append("| - | - | - | - |")
        return
    for item in evaluations:
        facts = safe_mapping(item.get("facts"))
        records = safe_sequence(item.get("records"))
        fact_text = " / ".join(
            part for part in (compact_facts(facts), compact_conditions(facts)) if part
        )
        record_text = compact_records(records)
        lines.append(
            "| "
            + " | ".join(
                [
                    CORE_LABEL_NAMES.get(str(item.get("key")), str(item.get("display_name") or item.get("key"))),
                    "[✓] 命中" if item.get("matched") else "[✗] 未命中",
                    fact_text or "-",
                    record_text or "-",
                ]
            )
            + " |"
        )


def append_audit_block(lines: list[str], wallet_result: Mapping[str, Any]) -> None:
    metrics = safe_mapping(wallet_result.get("metrics"))
    operation_audit = safe_mapping(wallet_result.get("operation_audit") or metrics.get("operation_audit"))
    profit_summary = safe_mapping(
        operation_audit.get("profit_summary") or metrics.get("audit_profit_summary")
    )
    if not profit_summary:
        return

    collection_status = safe_mapping(operation_audit.get("collection_status"))
    stop_parts = [
        f"{name}:{safe_mapping(status).get('stop_reason') or '-'}"
        for name, status in collection_status.items()
    ]
    lines.extend(
        [
            "",
            "#### 流水审计",
            "",
            f"- 抓取完整性：{'完整' if operation_audit.get('complete') else '可能截断'}",
            f"- 交易流动性收益：{format_currency(profit_summary.get('trade_liquidity_profit'))}，倍数 {format_multiple(profit_summary.get('trade_liquidity_profit_multiple'))}",
            f"- 最终兑换收益：{format_currency(profit_summary.get('final_settlement_profit'))}，倍数 {format_multiple(profit_summary.get('final_settlement_profit_multiple'))}",
            f"- 统一收益：{format_currency(profit_summary.get('unified_profit'))}，倍数 {format_multiple(profit_summary.get('unified_profit_multiple'))}",
            f"- 抓取停止原因：{'；'.join(stop_parts) if stop_parts else '-'}",
        ]
    )


def append_profile_block(lines: list[str], profile: Mapping[str, Any]) -> None:
    average_buy = safe_mapping(profile.get("average_buy_price"))
    buy_distribution = safe_mapping(profile.get("buy_price_distribution"))
    closed_pnl = safe_mapping(profile.get("closed_position_pnl"))
    top_cities = safe_mapping(profile.get("top_cities"))

    lines.extend(
        [
            "",
            "#### 画像指标",
            "",
            f"- 平均买入价：{format_number(average_buy.get('weighted_average_price'), 4)}，覆盖 {format_count(average_buy.get('priced_buy_count'))} 笔买入。",
            f"- 买入价格分布：{format_price_buckets(buy_distribution)}",
            f"- 已关闭仓位盈亏：{format_currency(closed_pnl.get('total_realized_pnl'))}，胜率 {format_percent(closed_pnl.get('win_rate'))}，盈亏倍数 {format_multiple(closed_pnl.get('profit_multiple'))}。",
            f"- Top 买入城市：{format_top_city_list(top_cities.get('by_buy_amount'), 'buy_amount')}",
            f"- Top 盈亏城市：{format_top_city_list(top_cities.get('by_realized_pnl'), 'realized_pnl')}",
        ]
    )


def append_record_samples(lines: list[str], wallet_result: dict[str, Any]) -> None:
    lines.extend(["", "#### 交易与头寸样本", ""])
    append_record_list(lines, "重点交易", wallet_result.get("top_trades", []), format_trade)
    append_record_list(lines, "当前持仓", wallet_result.get("top_positions", []), format_position)
    append_record_list(lines, "已平仓头寸", wallet_result.get("top_closed_positions", []), format_closed_position)


def append_record_list(
    lines: list[str],
    title: str,
    records: Any,
    formatter,
) -> None:
    lines.append(f"- {title}：")
    rows = [safe_mapping(record) for record in safe_sequence(records)]
    if not rows:
        lines.append("  - 无")
        return
    for record in rows[:5]:
        lines.append(f"  - {formatter(record)}")


def effective_core_rules(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    configured = {
        str(rule.get("key")): rule
        for rule in config.get("labels", [])
        if isinstance(rule, Mapping) and rule.get("enabled", True) is not False
    }
    rules: dict[str, dict[str, Any]] = {}
    for key in CORE_LABEL_KEYS:
        merged = dict(CORE_LABEL_DEFAULT_RULES[key])
        if key in configured:
            merged.update(configured[key])
            merged["key"] = key
        rules[key] = merged
    return rules


def format_rule_conditions(rule: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    all_conditions = safe_sequence(rule.get("all"))
    any_conditions = safe_sequence(rule.get("any"))
    if all_conditions:
        chunks.append("且 ".join(format_condition(condition) for condition in all_conditions))
    if any_conditions:
        chunks.append("或 ".join(format_condition(condition) for condition in any_conditions))
    return "；".join(chunk for chunk in chunks if chunk) or "无显式条件"


def format_condition(condition: Any) -> str:
    item = safe_mapping(condition)
    field = human_label(str(item.get("field") or "metric"))
    return f"{field} {item.get('op', '==')} {format_condition_value(item.get('value'))}"


def format_condition_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def label_source_text(key: str) -> str:
    sources = {
        "high_frequency_region": "regional_trade_summary.regions",
        "high_daily_region_profit": "regional_daily_profit_summary.region_days",
        "regional_high_win_rate": "regional_day_win_rate_summary.regions",
        "lottery_player": "low_chip_cost_summary.low_chip_records",
        "split_player": "split_position_average_cost_summary + chain_validation.evidence",
        "liquidity_player": "liquidity_player_summary.region_days",
    }
    return sources.get(key, "metrics")


def label_evaluations(wallet_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = wallet_result.get("label_evaluations")
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []

    labels = wallet_result.get("labels")
    if isinstance(labels, list):
        return [
            {
                "key": label.get("key"),
                "display_name": label.get("display_name"),
                "matched": True,
                "reason": label.get("description") or "命中配置标签。",
                "facts": {},
                "records": [],
            }
            for label in labels
            if isinstance(label, Mapping)
        ]
    return []


def latest_evidence_date(evaluations: list[dict[str, Any]], metrics: Mapping[str, Any]) -> str:
    candidates: list[str] = []
    for item in evaluations:
        facts = safe_mapping(item.get("facts"))
        if facts.get("date"):
            candidates.append(str(facts["date"]))
        for record in safe_sequence(item.get("records")):
            row = safe_mapping(record)
            for field in ("date", "buy_date", "high_temperature_date"):
                if row.get(field):
                    candidates.append(str(row[field]))
    if metrics.get("latest_trade_date"):
        candidates.append(str(metrics["latest_trade_date"]))
    return max(candidates) if candidates else ""


def compact_facts(facts: Mapping[str, Any]) -> str:
    keys = (
        "city",
        "region",
        "date",
        "ratio",
        "multiple",
        "trade_count",
        "numerator",
        "denominator",
        "buy_amount",
        "sell_amount",
        "average_chip_cost",
        "swap_ratio",
    )
    parts = []
    for key in keys:
        if key not in facts or facts[key] in (None, ""):
            continue
        parts.append(f"{human_label(key)}={format_fact_value(key, facts[key])}")
        if len(parts) >= 5:
            break
    return "；".join(parts)


def compact_conditions(facts: Mapping[str, Any]) -> str:
    conditions = [safe_mapping(item) for item in safe_sequence(facts.get("conditions"))]
    parts = []
    for condition in conditions[:2]:
        status = "✓" if condition.get("matched") else "✗"
        parts.append(
            f"{status} {human_label(str(condition.get('field') or 'metric'))} "
            f"{condition.get('op')} {condition.get('value')}（实际 {condition.get('actual')}）"
        )
    return "；".join(parts)


def compact_records(records: Sequence[Any]) -> str:
    parts = []
    for record in records[:2]:
        if isinstance(record, str):
            parts.append(record)
            continue
        row = safe_mapping(record)
        text = " ".join(
            str(value)
            for value in (
                row.get("date") or row.get("buy_date") or row.get("high_temperature_date"),
                row.get("city") or row.get("region"),
                format_fact_value("multiple", row.get("multiple")) if row.get("multiple") is not None else "",
                format_fact_value("ratio", row.get("ratio") or row.get("trade_ratio"))
                if row.get("ratio") is not None or row.get("trade_ratio") is not None
                else "",
                format_count(row.get("trade_count")) if row.get("trade_count") is not None else "",
            )
            if value not in (None, "")
        ).strip()
        if text:
            parts.append(text)
    return "；".join(parts)


def format_top_city_list(records: Any, field: str) -> str:
    rows = [safe_mapping(record) for record in safe_sequence(records)]
    if not rows:
        return "-"
    return "；".join(
        f"{row.get('city') or row.get('region') or '-'} {format_fact_value(field, row.get(field))}"
        for row in rows[:3]
    )


def format_price_buckets(distribution: Mapping[str, Any]) -> str:
    buckets = [safe_mapping(bucket) for bucket in safe_sequence(distribution.get("buckets"))]
    parts = [
        f"{format_number(bucket.get('min'), 2)}-{format_number(bucket.get('max'), 2)}: {format_count(bucket.get('count'))}笔"
        for bucket in buckets
        if number_value(bucket.get("count")) > 0
    ]
    return "；".join(parts[:5]) if parts else "-"


def format_trade(record: Mapping[str, Any]) -> str:
    side = str(record.get("side") or "-")
    title = str(record.get("title") or record.get("slug") or "-")
    outcome = str(record.get("outcome") or "-")
    notional = format_currency(record_notional(record))
    return f"{side} | {title} | {outcome} | {notional}"


def format_position(record: Mapping[str, Any]) -> str:
    title = str(record.get("title") or record.get("slug") or "-")
    outcome = str(record.get("outcome") or "-")
    current_value = format_currency(record.get("currentValue"))
    pnl = format_currency(record.get("cashPnl"))
    end_date = str(record.get("endDate") or "-")
    return f"{title} | {outcome} | 当前价值 {current_value} | 浮动盈亏 {pnl} | 到期 {end_date}"


def format_closed_position(record: Mapping[str, Any]) -> str:
    title = str(record.get("title") or record.get("slug") or "-")
    outcome = str(record.get("outcome") or "-")
    realized_pnl = format_currency(record.get("realizedPnl"))
    total_bought = format_currency(record.get("totalBought"))
    end_date = str(record.get("endDate") or "-")
    return f"{title} | {outcome} | 已实现盈亏 {realized_pnl} | 买入 {total_bought} | 到期 {end_date}"


def record_notional(record: Mapping[str, Any]) -> float:
    explicit = number_value(record.get("usdcSize"))
    if explicit > 0:
        return explicit
    size = number_value(record.get("size"))
    price = number_value(record.get("price"))
    if size > 0 and price > 0:
        return size * price
    for field in ("currentValue", "initialValue", "totalBought"):
        value = number_value(record.get(field))
        if value > 0:
            return value
    return 0.0


def mean_metric(wallet_results: list[dict[str, Any]], key: str) -> float:
    values = [number_value(wallet.get("metrics", {}).get(key)) for wallet in wallet_results]
    values = [value for value in values if value != 0.0]
    return sum(values) / len(values) if values else 0.0


def format_fact_value(key: str, value: Any) -> str:
    if key in {"ratio", "trade_ratio", "win_rate", "swap_ratio"}:
        return format_percent(value)
    if key in {"multiple", "profit_multiple", "highest_burst"}:
        return format_multiple(value)
    if any(token in key for token in ("amount", "pnl", "value", "cashflow", "buy", "sell")):
        return format_currency(value)
    if "count" in key or key in {"numerator", "denominator"}:
        return format_count(value)
    if isinstance(value, float):
        return format_number(value, 2)
    return str(value)


def format_currency(value: Any) -> str:
    return f"${number_value(value):,.2f} USDC"


def format_percent(value: Any) -> str:
    return f"{number_value(value) * 100:.1f}%"


def format_multiple(value: Any) -> str:
    return f"{number_value(value):.2f}x"


def format_count(value: Any) -> str:
    number = number_value(value)
    return f"{int(number):,}" if number.is_integer() else f"{number:,.2f}"


def format_number(value: Any, digits: int = 2) -> str:
    number = number_value(value)
    return f"{number:,.{digits}f}"


def number_value(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def short_address(value: str) -> str:
    if len(value) <= 12:
        return value or "-"
    return f"{value[:6]}...{value[-4:]}"


def safe_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def safe_sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def human_label(key: str) -> str:
    if key == "weather_trade_ratio":
        return "\u5929\u6c14\u8d5b\u9053\u4ea4\u6613\u5360\u6bd4"
    if key == "best_region_trade_count":
        return "\u5730\u533a\u4ea4\u6613\u7b14\u6570"
    if key == "trade_count":
        return "\u4ea4\u6613\u7b14\u6570"
    labels = {
        "dominant_region_trade_ratio": "主地区交易占比",
        "max_region_daily_profit_multiple": "最高地区日盈利倍数",
        "best_region_positive_return_day_ratio": "地区正收益天数占比",
        "low_chip_cost_trade_ratio": "低筹码成本交易占比",
        "split_player_validation_passed": "拆分验证",
        "liquidity_player_matched": "流动型验证",
        "city": "城市",
        "region": "地区",
        "date": "日期",
        "ratio": "占比",
        "multiple": "倍数",
        "numerator": "分子",
        "denominator": "分母",
        "buy_amount": "买入金额",
        "sell_amount": "卖出金额",
        "average_chip_cost": "平均筹码成本",
        "swap_ratio": "swap 占比",
    }
    return labels.get(key, key.replace("_", " "))


def append_region_path_table(lines: list[str], profile: Mapping[str, Any]) -> None:
    city_distribution = safe_mapping(profile.get("city_distribution"))
    cities = safe_sequence(city_distribution.get("cities"))
    lines.extend(
        [
            "",
            "#### \u5730\u533a\u4ea4\u6613\u5206\u5e03",
            "",
            "| \u5730\u533a | \u4ea4\u6613\u6570 | \u80dc\u7387 | \u4e70\u5165 | \u5356\u51fa | \u4ea4\u6613\u73b0\u91d1\u6d41 | \u5df2\u5e73\u4ed3\u76c8\u4e8f |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if not cities:
        lines.append("| - | 0 | - | - | - | - | - |")
        return
    for city in cities[:8]:
        row = safe_mapping(city)
        win_rate = format_percent(row.get("positive_return_day_ratio"))
        positive_days = format_count(row.get("positive_return_days"))
        total_days = format_count(row.get("total_trade_days"))
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("city") or row.get("region") or "-"),
                    format_count(row.get("trade_count")),
                    f"{win_rate} ({positive_days}/{total_days})",
                    format_currency(row.get("buy_amount")),
                    format_currency(row.get("sell_amount")),
                    format_currency(row.get("net_trade_cashflow")),
                    format_currency(row.get("realized_pnl")),
                ]
            )
            + " |"
        )


def append_filter_conditions(lines: list[str], config: dict[str, Any]) -> None:
    wallet_filter = config.get("wallet_filter", {})
    leaderboard = config.get("leaderboard", {})
    weather = config.get("weather", {})
    analysis = config.get("analysis", {})
    chain_validation = config.get("chain_validation", {})

    lines.extend(
        [
            "## \u7b5b\u9009\u6761\u4ef6",
            "",
            (
                f"- \u6392\u884c\u699c\u8303\u56f4\uff1acategory={leaderboard.get('category', '-')}, "
                f"time_period={leaderboard.get('time_period', '-')}, "
                f"order_by={leaderboard.get('order_by', '-')}"
            ),
            (
                f"- \u6293\u53d6\u9884\u7b97\uff1a\u6392\u884c\u699c\u524d {leaderboard.get('fetch_limit', '-')} \u6761\uff0c"
                f"\u5929\u6c14\u4e8b\u4ef6\u6700\u591a {weather.get('max_events', '-')} \u4e2a"
            ),
            f"- \u5165\u9009\u76ee\u6807\uff1a{wallet_filter.get('target_count', '-')} \u4e2a\u94b1\u5305",
            (
                f"- \u76c8\u5229\u533a\u95f4\uff1a{format_currency(wallet_filter.get('min_pnl'))} ~ "
                f"{format_currency(wallet_filter.get('max_pnl'))}"
            ),
            f"- \u4ea4\u6613\u91cf\u4e0a\u9650\uff1a{format_currency(wallet_filter.get('max_volume'))}",
            (
                f"- \u4ea4\u6613\u7b14\u6570\u533a\u95f4\uff1a{format_count(wallet_filter.get('min_traded_count'))} ~ "
                f"{format_count(wallet_filter.get('max_traded_count'))}"
            ),
            f"- \u5929\u6c14\u8d5b\u9053\u4ea4\u6613\u5360\u6bd4\u4e0b\u9650\uff1a{format_percent(wallet_filter.get('min_weather_trade_ratio'))}",
            f"- \u9ad8\u9891\u5730\u533a\u533a\u57df-\u65e5\u671f\u5360\u6bd4\u9608\u503c\uff1a{format_percent(analysis.get('regional_frequency_min_day_ratio'))}",
            f"- \u5730\u533a\u9ad8\u80dc\u7387\u6700\u5c0f\u4ea4\u6613\u7b14\u6570\uff1a{format_count(analysis.get('regional_win_rate_min_trade_count'))}",
            f"- \u5e76\u53d1\u94b1\u5305\u5206\u6790\uff1a{analysis.get('concurrent_wallets', '-')} \u4e2a",
            f"- \u94fe\u4e0a\u62c6\u5206\u6821\u9a8c\uff1a{'\u5f00\u542f' if chain_validation.get('enabled') else '\u5173\u95ed'}",
            "",
        ]
    )


def human_label(key: str) -> str:
    labels = {
        "dominant_region_trade_ratio": "\u4e3b\u5730\u533a\u533a\u57df-\u65e5\u671f\u5360\u6bd4",
        "weather_trade_ratio": "\u5929\u6c14\u8d5b\u9053\u4ea4\u6613\u5360\u6bd4",
        "max_region_daily_profit_multiple": "\u6700\u9ad8\u5730\u533a\u5355\u65e5\u76c8\u5229\u500d\u6570",
        "best_region_positive_return_day_ratio": "\u5730\u533a\u6b63\u6536\u76ca\u5929\u6570\u5360\u6bd4",
        "best_region_trade_count": "\u5730\u533a\u4ea4\u6613\u7b14\u6570",
        "low_chip_cost_trade_ratio": "\u4f4e\u6210\u672c\u4ea4\u6613\u5360\u6bd4",
        "split_player_validation_passed": "\u62c6\u5206\u9a8c\u8bc1",
        "liquidity_player_matched": "\u6d41\u52a8\u578b\u9a8c\u8bc1",
        "city": "\u57ce\u5e02",
        "region": "\u5730\u533a",
        "date": "\u65e5\u671f",
        "ratio": "\u5360\u6bd4",
        "multiple": "\u500d\u6570",
        "trade_count": "\u4ea4\u6613\u6570",
        "min_trade_count": "\u6700\u5c0f\u4ea4\u6613\u7b14\u6570",
        "positive_return_days": "\u6b63\u6536\u76ca\u5929\u6570",
        "total_trade_days": "\u4ea4\u6613\u5929\u6570",
        "numerator": "\u5206\u5b50",
        "denominator": "\u5206\u6bcd",
        "buy_amount": "\u4e70\u5165\u91d1\u989d",
        "sell_amount": "\u5356\u51fa\u91d1\u989d",
        "average_chip_cost": "\u5e73\u5747\u7b79\u7801\u6210\u672c",
        "swap_ratio": "swap \u5360\u6bd4",
    }
    return labels.get(key, key.replace("_", " "))


# Clean overrides for report text after legacy encoding damage above.
def append_filter_conditions(lines: list[str], config: dict[str, Any]) -> None:
    wallet_filter = config.get("wallet_filter", {})
    leaderboard = config.get("leaderboard", {})
    weather = config.get("weather", {})
    analysis = config.get("analysis", {})
    chain_validation = config.get("chain_validation", {})

    lines.extend(
        [
            "## 筛选条件",
            "",
            (
                f"- 排行榜范围：category={leaderboard.get('category', '-')}, "
                f"time_period={leaderboard.get('time_period', '-')}, "
                f"order_by={leaderboard.get('order_by', '-')}"
            ),
            (
                f"- 抓取预算：排行榜前 {leaderboard.get('fetch_limit', '-')} 条，"
                f"天气事件最多 {weather.get('max_events', '-')} 个"
            ),
            f"- 入选目标：{wallet_filter.get('target_count', '-')} 个钱包",
            (
                f"- 盈利区间：{format_currency(wallet_filter.get('min_pnl'))} ~ "
                f"{format_currency(wallet_filter.get('max_pnl'))}"
            ),
            f"- 交易量上限：{format_currency(wallet_filter.get('max_volume'))}",
            (
                f"- 交易笔数区间：{format_count(wallet_filter.get('min_traded_count'))} ~ "
                f"{format_count(wallet_filter.get('max_traded_count'))}"
            ),
            f"- 天气赛道交易占比下限：{format_percent(wallet_filter.get('min_weather_trade_ratio'))}",
            f"- 高频地区命中阈值：{format_percent(analysis.get('regional_frequency_min_day_ratio'))}（底层按地区-日期样本统计）",
            f"- 地区高胜率最小交易笔数：{format_count(analysis.get('regional_win_rate_min_trade_count'))}",
            f"- 并发钱包分析：{analysis.get('concurrent_wallets', '-')} 个",
            f"- 链上拆分校验：{'开启' if chain_validation.get('enabled') else '关闭'}",
            "",
        ]
    )


def human_label(key: str) -> str:
    labels = {
        "dominant_region_trade_ratio": "主地区占比",
        "weather_trade_ratio": "天气赛道交易占比",
        "max_region_daily_profit_multiple": "最高地区单日盈利倍数",
        "best_region_positive_return_day_ratio": "地区正收益天数占比",
        "best_region_trade_count": "地区交易笔数",
        "low_chip_cost_trade_ratio": "低成本交易占比",
        "split_player_validation_passed": "拆分验证",
        "liquidity_player_matched": "流动型验证",
        "city": "城市",
        "region": "地区",
        "date": "日期",
        "ratio": "占比",
        "multiple": "倍数",
        "trade_count": "交易笔数",
        "min_trade_count": "最小交易笔数",
        "numerator": "分子",
        "denominator": "分母",
        "buy_amount": "买入金额",
        "sell_amount": "卖出金额",
        "average_chip_cost": "平均筹码成本",
        "swap_ratio": "swap 占比",
    }
    return labels.get(key, key.replace("_", " "))


def append_region_path_table(lines: list[str], profile: Mapping[str, Any]) -> None:
    city_distribution = safe_mapping(profile.get("city_distribution"))
    cities = safe_sequence(city_distribution.get("cities"))
    lines.extend(
        [
            "",
            "#### 地区交易分布",
            "",
            "| 地区 | 交易数 | 胜率 | 买入 | 卖出 | 交易现金流 | 已平仓盈亏 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if not cities:
        lines.append("| - | 0 | - | - | - | - | - |")
        return
    for city in cities[:8]:
        row = safe_mapping(city)
        win_rate = format_percent(row.get("positive_return_day_ratio"))
        positive_days = format_count(row.get("positive_return_days"))
        total_days = format_count(row.get("total_trade_days"))
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("city") or row.get("region") or "-"),
                    format_count(row.get("trade_count")),
                    f"{win_rate} ({positive_days}/{total_days})",
                    format_currency(row.get("buy_amount")),
                    format_currency(row.get("sell_amount")),
                    format_currency(row.get("net_trade_cashflow")),
                    format_currency(row.get("realized_pnl")),
                ]
            )
            + " |"
        )
