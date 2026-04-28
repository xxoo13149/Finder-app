# Split Player Chain Validation Research

## Goal

Implement the split-player label conservatively: a wallet may be labeled only when its average open-position chip cost is near 5 and Polygon chain evidence confirms repeated Polymarket Neg Risk Adapter conversions.

## Evidence Standard

Use the official Polymarket Neg Risk Adapter on Polygon:

- Chain ID: `137`
- Neg Risk Adapter: `0xd91e80cf2e7be2e162c6513ced06f1dd0da35296`
- `PositionsConverted(address,bytes32,uint256,uint256)` topic0:
  `0xb03d19dddbc72a87e735ff0ea3b57bef133ebe44e1894284916a84044deb367e`

Strong evidence is a matching `PositionsConverted` log where:

- `log.address` is the official adapter
- `topic0` is the event signature above
- `topic1` is the target wallet address encoded as an indexed topic

Normal CTF token transfers, generic swaps, or position changes are not enough by themselves because they can come from buys, sells, transfers, split, merge, redeem, or relayed activity.

## API Path

Use Etherscan V2 compatible API with `chainid=137`:

- `module=logs&action=getLogs` for adapter `PositionsConverted` evidence
- `module=account&action=txlist` with `sort=asc&offset=1` for first normal Polygon transaction date

## Fail-Closed Semantics

Do not apply the label when:

- chain validation is disabled
- API key is missing
- request fails
- no matching adapter logs are found
- evidence count is below the configured minimum
- average open-position chip cost is not near the configured target

## Sources

- Polymarket negative-risk documentation: https://docs.polymarket.com/advanced/neg-risk
- Polymarket contracts: https://docs.polymarket.com/resources/contracts
- Polymarket positions and tokens: https://docs.polymarket.com/concepts/positions-tokens
- Neg Risk Adapter source: https://github.com/Polymarket/neg-risk-ctf-adapter/blob/main/src/NegRiskAdapter.sol
- Etherscan V2 txlist endpoint: https://docs.etherscan.io/api-reference/endpoint/txlist
- Etherscan supported chains: https://docs.etherscan.io/supported-chains
