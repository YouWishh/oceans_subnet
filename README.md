<div align="center">
<picture>
  <source srcset="image.png" media="(prefers-color-scheme: dark)">
  <source srcset="image.png" media="(prefers-color-scheme: light)">
  <img
    src="image.png"
    width="192"
    alt="Company logo"
    style="border-radius:50%; display:block">
</picture>

# **Oceans Subnet 66** <!-- omit in toc -->

[![Discord Chat](https://img.shields.io/discord/308323056592486420.svg)](https://discord.gg/bittensor)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Twitter Follow](https://img.shields.io/twitter/follow/OceansSN66?style=social)](https://twitter.com/OceansSN66)

🌐 [Whitepaper](https://oceans66.com/whitepaper/introduction) • ⛏️ [Mining Guide](docs/miner.md) • 🧑‍🏫 [Validator Guide](docs/validator.md)

</div>

---

## 📋 Overview

Oceans is on a mission to transform bittensor pools into oceans. We will use bittensor incentives to channel liquidity to where it is most productive. Subnet liquidity has been a huge issue in Bittensor and we want to solve it.

### Key Components:

- **🗳️ Holders of sn66 alpha** - Vote (with their vote relevance determined by their sn66 α-Stake) and decide to which bittensor subnet miners should provide liquidity to.

- **⛏️ Miners of sn66** - Provide liquidity to bittensor subnets and will be rewarded depending on the amount of liquidity provided and the weight of each subnet determined by the votes of the sn66 holders.

### Reward Factors:

**📊 Liquidity Amount**  
How much liquidity they provide to the pools, measured as the total value in TAO of their positions

**🎯 Pool Selection**  
Whether the pools they supply were selected by the α-Stake voting of the holders

> The result is an incentivized system that continually redirects liquidity toward the pools most valued by the community.

## 🔄 Incentive Mechanism

Oceans' goal is to incentivize miners to provide liquidity where the community decides. The incentive mechanism is implemented in three steps:

### 1. **🗳️ Governance**

Token holders continuously cast votes to express the relative importance of each subnet. The voting process generates a normalized weight vector `W = [W₁, W₂, ..., Wₙ]`, which is publicly available and auditable.

### 2. **💧 Provide Liquidity**

Miners fetch the current subnet weights vector and decide where to provide liquidity. Miners have to decide which subnets are better based on its weights and the current market situation. α-Stake votes will route the emissions, but miners are the ones ultimately deciding.

### 3. **💰 Reward**

Every epoch, the subnet measures the total liquidity provided by the miners, and distributes incentives based on the liquidity provided and the weights holders voted.

**Reward Formula:**

![Miner rewards formula](formula.png)

Where:

- `Iᵢ,ₖ` = Incentive for miner i in pool k
- `E` = Total emissions for the epoch
- `Wₖ` = Weight of pool k (from voting)
- `Lᵢ,ₖ` = Liquidity provided by miner i to pool k
- `Lₖᵗᵒᵗᵃˡ` = Total liquidity in pool k

## 🗳️ Voting Mechanism

Sn66 alpha works as a **governance token** where holders vote which subnets they want miners to provide liquidity to. This alpha token will be valuable as it can be used to increase liquidity in the targeted pools providing **price stability** and reduced **slippage**.

### Voting Features:

- **Weight Allocation** - Change subnet weights allocation to direct where liquidity flows
- **Community Tracking** - Track other people's votes and see community sentiment in real-time
- **Global Overview** - See current global weights where holders want miners to provide liquidity
- **Live Monitoring** - Monitor current miners liquidity flow and actual deployment vs votes

### Dashboard Preview

![Dashboard Modal Screenshot](dashboard.png)

### 🔍 Full Transparency

Because the votes are public, anyone can reproduce them and verify that the weight allocation was correct.

### 🚀 Future Decentralization

Moving towards fully decentralized governance is a core priority. By transitioning voting mechanisms to smart contracts, we eliminate centralized points of failure and ensure complete transparency and immutability of the governance process.

## 💎 Utility Model

Sn66 alpha is a governance token that can be seen as **"bandwidth rights"** of Sn66 commodity. Sn66 commodity is liquidity and so the holders of sn66 alpha can decide where this liquidity flows.

### Key Benefits:

- **🔄 Liquidity Allocation Rights** - Holders allocate token-weighted votes that guide where miners should provide liquidity
- **📈 Price Stability** - Strategic liquidity placement reduces slippage and enhances price stability
- **⚔️ Miner Competition** - Miners compete to supply the most liquidity in holder-preferred pools

> Ocean Subnet does not generate fees, it creates a valuable **commodity** as it is liquidity. **Buy pressure** will come from people who want to dispose and consume this commodity.

## 🎮 Liquidity Boosts

We're introducing **gamification features** on the subnet—holders will be able to "burn" a small amount of SN66 α in exchange for a **time-limited liquidity boost**. This lets active participants amplify liquidity for their preferred pools.

## 🗺️ Roadmap

| Version | Timing         | Features                                                                                                                                                                                    | Status         |
| ------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| **V1**  | July 2025      | • Off-chain web voting by α-Stake holders<br>• Validators fetch votes off-chain & fetch liquidity on-chain to determine weights                                                             | ✅ Completed   |
| **V2**  | July 2025      | • Dashboard for holders and miners<br>• Monitoring votes and liquidity positions                                                                                                            | 🔄 In Progress |
| **V3**  | August 2025    | • Migrate α-Stake voting to fully on-chain smart contracts<br>• Validators become light verifiers of on-chain data only                                                                     | 🔜 Upcoming    |
| **V4**  | August 2025    | • Introduce burn-based bounty mechanism (SN α → pool premium)<br>• Burned SN α temporarily augments stake-consensus weight<br>• UI & validator support for bounty tracking and decay timers | 🔜 Upcoming    |
| **V5**  | September 2025 | • Move voting logic fully on-chain<br>• Eliminate off-chain dependencies and trust assumptions                                                                                              | 🔜 Upcoming    |

> **ℹ️ Toward Full Decentralization:** Moving voting logic **on-chain** eliminates the need for validators to fetch and mirror off-chain votes, reducing **trust assumptions** and completing the governance loop entirely within the blockchain.

## 🚀 Getting Started

### For Miners

1. Check the [Mining Guide](docs/miner.md)
2. Set up your liquidity provision infrastructure
3. Monitor the voting weights dashboard
4. Optimize your liquidity allocation based on community votes

### For Holders

1. Acquire SN66 α tokens
2. Access the voting dashboard at [oceans66.com](https://oceans66.com)
3. Allocate your voting weights to preferred subnets
4. Monitor liquidity flows and adjust votes as needed

### For Validators

1. Review the [Validator Guide](docs/validator.md)
2. Set up your validation node
3. Monitor and verify voting weights
4. Calculate and distribute miner rewards

## 📊 Key Metrics

- **Total Value Locked (TVL)**: Track the total liquidity provided across all pools
- **Active Voters**: Number of unique addresses participating in governance
- **Liquidity Distribution**: Real-time view of liquidity allocation vs voting weights
- **Miner Performance**: Rankings based on liquidity provision efficiency

## 🤝 Community

- **Discord**: [Join our community](https://discord.gg/bittensor)
- **Twitter**: [@OceansSN66](https://twitter.com/OceansSN66)
- **GitHub**: [OceansSN66](https://github.com/OceansSN66)
- **Whitepaper**: [Read the full whitepaper](https://oceans66.com/whitepaper/introduction)
