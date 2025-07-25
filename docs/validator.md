# 🌊 Oceans Subnet 66 • Validator Guide (v0)

**Last updated:** July 2025  
**Applies to:** Oceans Subnet 66 (Bittensor Network, version 0)

---

## 🚀 1. How to Install

```bash
# Clone the repository
git clone https://github.com/Oceans-Subnet/oceans_subnet
cd oceans66

# Install general system dependencies
chmod +x scripts/validator/install_dependencies.sh
./scripts/validator/install_dependencies.sh

# Setup Python environment and packages
chmod +x scripts/validator/setup.sh
./scripts/validator/setup.sh
```

---

## ⚙️ 2. Run the Validator

### 🟢 PM2 Launch Example

```bash
# Activate Python environment
source validator_env/bin/activate

# Start validator with PM2
pm2 start neurons/validator.py --name oceans_validator -- \
  --netuid 66 \
  --subtensor.network finney \
  --wallet.name coldkey \
  --wallet.hotkey hotkey \
  --logging.debug
```

### 📋 Logs:

```bash
pm2 logs oceans_validator
```

### 🔄 Stop / Restart:

```bash
pm2 restart oceans_validator
pm2 stop     oceans_validator
```

---

## 🎯 3. High-Level Validator Explanation

### 1️⃣ **Vote Ingestion**

- 📊 **Fetch the weight vector** that α‑Stake holders set via off‑chain web voting
- 🔍 Make the vector publicly available and auditable

### 2️⃣ **Liquidity Measurement**

- 💧 **Observe on‑chain liquidity** that each registered miner supplies to eligible Bittensor subnet pools
- 💵 Convert each miner's positions to a USD valuation using the reference price oracle defined in the protocol

### 3️⃣ **Reward Calculation & Attribution**

- ⚖️ Combine the **community weight vector** with **measured liquidity per miner** to compute each miner's reward for the epoch

---

## 🤝 4. Support & Community

- **Discord:** [Join our channel](https://discord.com/channels/799672011265015819/1392960766990221312) 💬
- **Twitter:** [@OceansSN66](https://twitter.com/OceansSN66) 🐦
- **Website:** [oceans66.com](https://oceans66.com) 🌐

---

### 🙏 Acknowledgements

Thanks to the Bittensor core team and every operator striving for transparent, community‑driven liquidity. Together we keep the **oceans** deep, clear, and fair.

_Secure validating!_ 🛡️
