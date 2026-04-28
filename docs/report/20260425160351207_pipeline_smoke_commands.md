# Pipeline smoke and analysis commands

These commands keep the regression path small and avoid real network calls by default.

## Run the no-network regression tests

PowerShell:

```powershell
python -m unittest tests.test_upgrade_behaviors tests.test_pipeline_smoke
```

Bash:

```bash
python -m unittest tests.test_upgrade_behaviors tests.test_pipeline_smoke
```

## Optional tiny live smoke run

Use this only when live Polymarket API access is acceptable.

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m polymarket_weather_tool --target-count 1 --fetch-limit 3 --disable-cache --output-dir artifacts/manual-smoke
```

Bash:

```bash
export PYTHONPATH=src
python -m polymarket_weather_tool --target-count 1 --fetch-limit 3 --disable-cache --output-dir artifacts/manual-smoke
```

## Analyze generated wallet JSON

Set the artifact folder first.

PowerShell:

```powershell
$out = "artifacts/manual-smoke"
```

Bash:

```bash
export out="artifacts/manual-smoke"
```

### Conditional screening

PowerShell:

```powershell
python -c "import json, pathlib; p=pathlib.Path(r'$out')/'screening_records.json'; rows=json.loads(p.read_text(encoding='utf-8')); [print(r['wallet'], r['pnl'], r['volume'], r['trade_count'], r['selected'], ';'.join(r['reasons'])) for r in rows if r['pnl']>=1000 and r['volume']>=10000 and r['trade_count']>=25]"
```

Bash:

```bash
python -c 'import json, pathlib, os; p=pathlib.Path(os.environ.get("out","artifacts/manual-smoke"))/"screening_records.json"; rows=json.loads(p.read_text(encoding="utf-8")); [print(r["wallet"], r["pnl"], r["volume"], r["trade_count"], r["selected"], ";".join(r["reasons"])) for r in rows if r["pnl"]>=1000 and r["volume"]>=10000 and r["trade_count"]>=25]'
```

### Profit multiple

PowerShell:

```powershell
python -c "import json, pathlib; root=pathlib.Path(r'$out')/'wallets'; [print(p.stem, json.loads(p.read_text(encoding='utf-8'))['metrics'].get('profit_multiple', 0)) for p in root.glob('*.json')]"
```

Bash:

```bash
python -c 'import json, pathlib, os; root=pathlib.Path(os.environ.get("out","artifacts/manual-smoke"))/"wallets"; [print(p.stem, json.loads(p.read_text(encoding="utf-8"))["metrics"].get("profit_multiple", 0)) for p in root.glob("*.json")]'
```

### Win rate

PowerShell:

```powershell
python -c "import json, pathlib; root=pathlib.Path(r'$out')/'wallets'; [print(p.stem, json.loads(p.read_text(encoding='utf-8'))['metrics']['closed_position_win_rate']) for p in root.glob('*.json')]"
```

Bash:

```bash
python -c 'import json, pathlib, os; root=pathlib.Path(os.environ.get("out","artifacts/manual-smoke"))/"wallets"; [print(p.stem, json.loads(p.read_text(encoding="utf-8"))["metrics"]["closed_position_win_rate"]) for p in root.glob("*.json")]'
```

### Cost distribution

PowerShell:

```powershell
python -c "import json, pathlib; root=pathlib.Path(r'$out')/'wallets'; [print(p.stem, json.dumps(json.loads(p.read_text(encoding='utf-8'))['metrics'].get('cost_basis_distribution', {}), ensure_ascii=False)) for p in root.glob('*.json')]"
```

Bash:

```bash
python -c 'import json, pathlib, os; root=pathlib.Path(os.environ.get("out","artifacts/manual-smoke"))/"wallets"; [print(p.stem, json.dumps(json.loads(p.read_text(encoding="utf-8"))["metrics"].get("cost_basis_distribution", {}), ensure_ascii=False)) for p in root.glob("*.json")]'
```

### Frequency analysis

PowerShell:

```powershell
python -c "import json, pathlib; root=pathlib.Path(r'$out')/'wallets'; [print(p.stem, json.dumps(json.loads(p.read_text(encoding='utf-8'))['metrics'].get('trade_frequency', {}), ensure_ascii=False)) for p in root.glob('*.json')]"
```

Bash:

```bash
python -c 'import json, pathlib, os; root=pathlib.Path(os.environ.get("out","artifacts/manual-smoke"))/"wallets"; [print(p.stem, json.dumps(json.loads(p.read_text(encoding="utf-8"))["metrics"].get("trade_frequency", {}), ensure_ascii=False)) for p in root.glob("*.json")]'
```
