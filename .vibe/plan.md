# 🎯 FINAL SYSTEM PROMPT: RestBench Ultimate Agent

**YOU ARE MISTRAL VIBE.** Your mission: Build a complete, production-ready RestBench agent that **survives all scenarios and scores >0**. 
**Read the README.md, the STRATEGY_GUIDE.md or AGENT_CONTRACT.md if you need more context about the project".
** I already implemented the "## Get Started (5 minutes)" setup. **
---

## 🚨 NON-NEGOTIABLE CONSTRAINTS

1. **Branch:** `experiment/ultimate-agent` (user will create)
2. **LLM Config:** Use existing env vars (DO NOT modify):
  ```python
   MODEL = os.getenv("AGENT_MODEL", "openai/gpt-4.1-mini")
   OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
   OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://litellm-production.eba-pvykax23.eu-west-1.elasticbeanstalk.com/")
  ```
3. **Target:** Generalize across ALL scenarios (including hidden ones). **Avoid bankruptcy at all costs.**
4. **Write EVERYTHING:** All code, configs, documentation
5. **Logging:** Standard Python logging with INFO/DEBUG levels
6. **No Tests:** Skip unit/integration tests
7. **Error Handling:** Graceful degradation (try/except, return empty list on errors)
8. **Deliverable:** Just the code (no extra fluff)

---

## 📁 FINAL PROJECT STRUCTURE

```
restbench-team/
├── agents/
│   ├── __init__.py
│   ├── baseline/                      # ✅ ORIGINALS - DO NOT TOUCH
│   │   ├── __init__.py
│   │   ├── compare.py
│   │   ├── do_nothing.py
│   │   ├── evaluate.py
│   │   ├── llm_template.py
│   │   ├── my_agent.py
│   │   ├── naive_rule.py
│   │   ├── runner.py
│   │   ├── starter_template.py
│   │   └── test_connection.py
│   │
│   ├── prompts/                       # ✨ NEW: LLM prompt definitions
│   │   ├── __init__.py
│   │   ├── pricing_prompt.py         # Exported PRICING_PROMPT variable
│   │   └── satisfaction_prompt.py    # Exported SATISFACTION_PROMPT variable
│   │
│   ├── team/                          # ✨ NEW: Production modules
│   │   ├── __init__.py
│   │   ├── forecast.py               # Person 1: Demand prediction
│   │   ├── inventory.py              # Person 2: Stock management
│   │   ├── satisfaction.py           # Person 3: Satisfaction tracking
│   │   ├── helper.py                 # Person 4: Pricing/promotions
│   │   ├── agent.py                  # Main orchestrator
│   │   └── conflict_resolver.py      # Action conflict resolution
│   │
│   └── experiments/                  # Sandbox (keep existing)
│
├── core/                            # ✨ NEW: Shared utilities
│   ├── __init__.py
│   ├── observation.py               # Observation parsing helpers
│   └── utils.py                     # Shared functions
│
├── configs/                         # ✨ NEW: Configuration
│   └── default.yaml                 # Tunable parameters
│
├── .env.example                     # ✨ NEW: Environment template
├── requirements.txt                 # ✨ MODIFY: Add new deps
└── README.md                        # ✨ MODIFY: Add setup instructions
```

---

## 🎯 CORE ARCHITECTURE

### Sequential Pipeline (NOT Multi-Agent)

```
Observation → Forecast → Inventory → Satisfaction → Helper → Conflict Resolution → Actions
```

### Why This Architecture?

- ✅ Simple to implement in 1 pass
- ✅ Clear dependencies (inventory uses forecast)
- ✅ Easy to debug
- ✅ Fast execution (<30s per turn)
- ✅ Works with or without LLM

---

## 📦 MODULE SPECIFICATIONS

---

### 🔮 MODULE 1: forecast.py (Person 1)

**Goal:** Predict daily demand using all available signals

**Input:** `observation: dict`, `day: int`  
**Output:** `dict` with demand predictions

**Key Signals to Use:**

```python
observation["day_of_week"]          # Mon-Sun patterns
observation["weather_today"]         # Today's weather
observation["weather_forecast"]      # Next 3 days
observation["customer_trend"]       # Declining/Stable/Growing
observation["reputation_band"]      # Poor-Excellent
observation["recent_reviews"]       # Stars (delayed)
observation["service_summary"]      # Historical covers from notes
observation["alerts"]               # Scenario events
observation["notes"]                # Persistent memory
```

**Multipliers:**

```python
dow_multipliers = {
    "Monday": 0.80, "Tuesday": 0.85, "Wednesday": 0.90,
    "Thursday": 1.00, "Friday": 1.20, "Saturday": 1.40, "Sunday": 1.30
}
weather_multipliers = {"sunny": 1.00, "cloudy": 0.95, "rainy": 0.80, "stormy": 0.70}
trend_multipliers = {"Declining": 0.90, "Stable": 1.00, "Growing": 1.10}
rep_multipliers = {"Poor": 0.70, "Fair": 0.85, "Good": 1.00, "Very Good": 1.10, "Excellent": 1.20}
```

**Output Format:**

```python
{
    "today": {
        "day": 5,
        "day_of_week": "Friday",
        "predicted_demand": 120,      # Expected covers
        "confidence": "high",         # high/medium/low
        "weather": "sunny"
    },
    "next_3_days": [
        {"day": 6, "day_of_week": "Saturday", "predicted_demand": 140, "weather": "cloudy"},
        {"day": 7, "day_of_week": "Sunday", "predicted_demand": 130, "weather": "rainy"},
        {"day": 8, "day_of_week": "Monday", "predicted_demand": 90, "weather": "sunny"}
    ],
    "historical_avg_covers": 105,
    "is_weekend": True
}
```

**Critical:** Parse historical covers from `notes` field to improve accuracy

---

### 📦 MODULE 2: inventory.py (Person 2)

**Goal:** Never run out of ingredients, minimize waste

**Input:** `observation: dict`, `day: int`, `forecast: dict`  
**Output:** `List[dict]` of `place_order` actions

**Key Logic:**

1. **Calculate consumption rate** per ingredient from `menu_book`
2. **Use forecast** to predict demand for next 3-4 days
3. **Maintain safety stock** (25% buffer)
4. **Respect supplier constraints** (`min_order_kg`, delivery schedules)
5. **Avoid double-ordering** (check `pending_orders`)
6. **Track expiry dates** (FIFO consumption - oldest batches expire first)
7. **Diversify suppliers** (use top 2 most reliable)
8. **Monitor `dishes_unavailable_at**` (PRIMARY SIGNAL - if ingredient caused stockout, order aggressively)

**Supplier Reliability Tracking:**

```python
supplier_reliability = {}
for delivery in observation["delivery_history"]:
    supplier = delivery["supplier"]
    supplier_reliability[supplier] = {
        "total": supplier_reliability.get(supplier, {}).get("total", 0) + 1,
        "success": supplier_reliability.get(supplier, {}).get("success", 0) + (1 if delivery["on_time"] else 0)
    }
```

**Order Quantity Logic:**

```python
# For each ingredient:
required_kg = consumption_per_cover * total_predicted_demand
reorder_point = required_kg * 1.25  # 25% safety buffer

if usable_stock < reorder_point:
    order_qty = max(required_kg * 0.8, min_order_kg)
    # Don't spend >70% of cash on one order
    if order_qty * price > cash * 0.7:
        order_qty = (cash * 0.7) / price
```

**Critical:** If ingredient in `dishes_unavailable_at`, increase buffer to 50% and order immediately

---

### 😊 MODULE 3: satisfaction.py (Person 3)

**Goal:** Maintain reputation ≥ "Good", minimize walkouts

**Input:** `observation: dict`, `day: int`  
**Output:** `List[dict]` of satisfaction-related actions

**Key Logic:**

**Staffing:**

```python
new_staff = staff_level

# Weekend boost
if dow in ["Friday", "Saturday", "Sunday"]:
    new_staff = max(new_staff, 10)

# Walkout response (URGENT)
if walkouts in ["Some", "Many"] or avg_wait > 10:
    new_staff = min(new_staff + 2, 15)

# Cost optimization
if walkouts == "None" and avg_wait < 5 and dow not in ["Friday", "Saturday", "Sunday"]:
    new_staff = max(new_staff - 1, 5)  # Never < 5
```

**Reputation Protection:**

```python
if recent_reviews:
    avg_stars = sum(r["stars"] for r in recent_reviews) / len(recent_reviews)
    if avg_stars < 3.5:
        # Boost satisfaction
        if cash > 5000:
            actions.append({"tool": "set_marketing_spend", "args": {"amount": 300}})
        if active_menu:
            actions.append({"tool": "offer_daily_special", "args": {"dish": active_menu[0]}})
```

**Daily Actions:**

- Always offer a daily special (rotate through menu)
- Use happy hour on slow days (Mon-Wed) if affordable
- Save satisfaction metrics to `notes` for trend tracking

---

### 🛠️ MODULE 4: helper.py (Person 4)

**Goal:** Dynamic pricing, marketing, scenario handling

**Input:** `observation: dict`, `day: int`  
**Output:** `List[dict]` of pricing/marketing actions

**Pricing Strategy (Rule-Based Fallback):**

```python
for dish in active_menu:
    base_price = menu_book[dish]["base_price"]
    count = dishes_sold.get(dish, 0)
    new_price = base_price
    
    # Popularity
    if count > 20: new_price = base_price * 1.10
    elif count > 10: new_price = base_price * 1.05
    elif count < 5: new_price = base_price * 0.90
    elif count < 3: new_price = base_price * 0.85
    
    # Weather
    if weather in ["rainy", "stormy"]: new_price *= 0.95
    
    # Trend
    if trend == "Declining": new_price *= 0.90
    elif trend == "Growing": new_price *= 1.05
    
    # Clamp to 0.8x-1.2x range
    new_price = max(base_price * 0.8, min(new_price, base_price * 1.2))
    
    if abs(new_price - current_price) > 0.50:
        actions.append({"tool": "set_price", "args": {"dish": dish, "price": round(new_price, 2)}})
```

**LLM Pricing (If API Key Available):**

```python
# In helper.py:
if os.getenv("OPENAI_API_KEY"):
    try:
        from agents.prompts.pricing_prompt import PRICING_PROMPT
        pricing_actions = llm_pricing(observation, day)
    except:
        pricing_actions = rule_based_pricing(observation, day)
else:
    pricing_actions = rule_based_pricing(observation, day)
```

**Marketing Budget:**

```python
if dow in ["Monday", "Tuesday", "Wednesday"]:
    base_budget = min(400, cash * 0.05)
else:
    base_budget = min(200, cash * 0.03)

if trend == "Declining": base_budget *= 1.5
if recent_reviews and avg_stars < 3.5: base_budget *= 1.5

return max(0, min(base_budget, 500))
```

**Scenario Handling:**

```python
for alert in alerts:
    if "supplier" in alert.lower() and "outage" in alert.lower():
        # Diversify suppliers (handled in inventory.py)
        save_notes(f"Day {day}: SUPPLIER ALERT - {alert}")
    elif "tourist" in alert.lower() or "surge" in alert.lower():
        actions.append({"tool": "set_staff_level", "args": {"level": 12}})
    elif "renovation" in alert.lower():
        actions.append({"tool": "set_staff_level", "args": {"level": 6}})
```

---

### 🔄 MODULE 5: conflict_resolver.py

**Goal:** Resolve overlapping actions from different modules

**Conflict Resolution Rules:**


| Tool                  | Resolution Strategy                            |
| --------------------- | ---------------------------------------------- |
| `place_order`         | SUM quantities for same (supplier, ingredient) |
| `set_staff_level`     | Take HIGHEST (most conservative)               |
| `set_price`           | Take LAST (most recent decision)               |
| `set_marketing_spend` | Take HIGHEST                                   |
| `set_menu`            | Take LAST                                      |
| `offer_daily_special` | Take LAST                                      |
| `run_happy_hour`      | Take FIRST (only need once)                    |
| `save_notes`          | CONCATENATE all notes (max 4000 chars)         |


---

### 🎯 MODULE 6: agent.py (Main Orchestrator)

**Goal:** Combine all modules, log everything, run the game

**Structure:**

```python
import logging
from agents.team.forecast import predict_demand
from agents.team.inventory import manage_inventory
from agents.team.satisfaction import track_satisfaction
from agents.team.helper import handle_helper_tasks
from agents.team.conflict_resolver import resolve_conflicts
from agents.runner import run_game

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def strategy(observation: dict, day: int) -> list:
    logger.info(f"=== Day {day} ===")
    
    # 1. Forecast
    forecast = predict_demand(observation, day)
    
    # 2. Inventory
    actions = manage_inventory(observation, day, forecast)
    
    # 3. Satisfaction
    actions.extend(track_satisfaction(observation, day))
    
    # 4. Helper
    actions.extend(handle_helper_tasks(observation, day))
    
    # 5. Resolve conflicts
    actions = resolve_conflicts(actions)
    
    return actions

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="baseline")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--team-name", default="team_ultimate")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    result = run_game(strategy, team_name=args.team_name, scenario=args.scenario, seed=args.seed)
    print(f"Score: {result['score']['total_score']}")
```

---

## 📜 PROMPT FILES (agents/prompts/)

### pricing_prompt.py

```python
"""Pricing LLM Prompt - Import this variable, don't hardcode in helper.py"""

PRICING_PROMPT = """
You are an expert restaurant pricing strategist for an Italian restaurant.

ANALYZE THE FOLLOWING DATA AND SUGGEST OPTIMAL PRICE ADJUSTMENTS:

OBSERVATION DATA:
{observation}

ACTIVE MENU: {active_menu}

DISHES SOLD YESTERDAY: {dishes_sold}

MENU DETAILS (base prices, ingredients):
{menu_book}

RULES:
1. Return ONLY a JSON array of actions, or [] if no changes
2. Each action: {{"tool": "set_price", "args": {{"dish": "Dish Name", "price": 12.34}}}}
3. Prices MUST be between 0.8x and 1.2x the base price
4. Consider: popularity, weather, customer trend, day of week
5. Popular dishes (>15 sold): Can increase price (max 1.2x)
6. Unpopular dishes (<5 sold): Should decrease price (min 0.8x)
7. Rainy/stormy weather: Consider discounts
8. Declining trend: More aggressive discounts
9. Growing trend: Can charge premium

EXAMPLE OUTPUT:
[{{"tool": "set_price", "args": {{"dish": "Pizza Margherita", "price": 15.50}}}}, {{"tool": "set_price", "args": {{"dish": "Grilled Salmon", "price": 22.00}}}]

DO NOT include any explanation, markdown, or text outside the JSON array.
"""
```

### satisfaction_prompt.py (Optional - for future use)

```python
"""Satisfaction LLM Prompt"""

SATISFACTION_PROMPT = """
You are a restaurant operations expert analyzing customer satisfaction.

OBSERVATION:
{observation}

IDENTIFY THE BIGGEST SATISFACTION ISSUES AND SUGGEST ACTIONS.

Focus on:
- Staffing levels (current: {staff_level})
- Walkout rates ({walkouts})
- Wait times (avg: {avg_wait}, peak: {peak_wait})
- Reputation ({reputation})
- Recent reviews ({recent_reviews})

Suggest actions from: set_staff_level, run_happy_hour, offer_daily_special, set_marketing_spend

Return ONLY a JSON array of actions.
"""
```

---

## 📁 FILES TO CREATE

### New Directories:

```bash
mkdir -p agents/prompts agents/team core configs
```

### New Files:

1. `agents/__init__.py`
2. `agents/prompts/__init__.py`
3. `agents/prompts/pricing_prompt.py`
4. `agents/team/__init__.py`
5. `agents/team/forecast.py`
6. `agents/team/inventory.py`
7. `agents/team/satisfaction.py`
8. `agents/team/helper.py`
9. `agents/team/agent.py`
10. `agents/team/conflict_resolver.py`
11. `core/__init__.py`
12. `core/observation.py` (optional helpers)
13. `core/utils.py` (optional shared functions)
14. `configs/default.yaml`
15. `.env.example`

### Modified Files:

1. `requirements.txt` - Add: `pyyaml>=6.0`
2. `agents/runner.py` - Enhance error handling

### Unchanged Files:

- All files in `agents/baseline/`
- `agents/compare.py`
- `agents/evaluate.py`
- `agents/test_connection.py`

---

## 🎯 CRITICAL REQUIREMENTS

### ✅ MUST DO:

1. **Prevent bankruptcy** - Maintain €2,000+ cash reserve
2. **Prevent stockouts** - Use `dishes_unavailable_at` signal
3. **Check pending_orders** - Avoid double-ordering
4. **Validate all actions** - Names are case-sensitive
5. **Handle all scenarios** - baseline, supply_crisis, tourist_season, renovation, hidden
6. **Use notes field** - Track patterns across days

### ⚠️ MUST AVOID:

1. **Staff < 5** - Can't serve customers
2. **Prices outside 0.8x-1.2x** - Actions rejected
3. **Menu < 5 dishes** - Actions rejected
4. **Spending >70% cash** - Risk of bankruptcy
5. **Hardcoded supplier/ingredient names** - Use observation data
6. **No error handling** - Always catch exceptions
7. **Blocking on LLM** - Use timeouts

---

## 📊 SUCCESS CRITERIA


| Scenario       | Target Score        | Priority |
| -------------- | ------------------- | -------- |
| All            | > -10,000 (survive) | P0       |
| All            | > 0 (break even)    | P0       |
| baseline       | > 5,000             | P1       |
| supply_crisis  | > 0                 | P1       |
| tourist_season | > 0                 | P1       |
| renovation     | > 0                 | P1       |


---

## 🚀 EXECUTION COMMANDS

```bash
# Create branch (user will do this)
git checkout -b experiment/ultimate-agent

# Create structure
mkdir -p agents/prompts agents/team core configs
touch agents/prompts/__init__.py agents/team/__init__.py core/__init__.py

# Move existing files to baseline
mv agents/*.py agents/baseline/ 2>/dev/null || true

# Create all new files (see specifications above)

# Test
python -m agents.team.agent --scenario baseline --seed 42 --verbose
python -m agents.team.agent --scenario supply_crisis --seed 42
python -m agents.team.agent --scenario tourist_season --seed 42
python -m agents.team.agent --scenario renovation --seed 42

# Evaluate
python scripts/evaluate.py --agents team/agent --scenarios baseline,supply_crisis,tourist_season,renovation --seeds 42,88,123
```

---

## 💡 PRO TIPS

1. **Start with inventory** - Stockouts kill score fastest
2. **Use `dishes_unavailable_at**` - Clearest stockout signal
3. **Keep cash reserve** - €2,000 minimum
4. **Read `alerts**` - Scenario events appear here
5. **Track patterns in `notes**` - Persistent memory
6. **Diversify suppliers** - Don't rely on one
7. **Weekend staffing** - Fri/Sat/Sun = more staff
8. **Test with seeds 42, 88, 123** - Reproducible results

---

## 🏆 FINAL CHECKLIST

- Branch created: `experiment/ultimate-agent`
- Directory structure created
- All 6 team modules implemented
- Prompt files in `agents/prompts/`
- Conflict resolver working
- Main agent orchestrator working
- Logging configured
- Error handling in all modules
- Tested on baseline, seed 42
- Tested on all scenarios
- Score > 0 on all scenarios
- No bankruptcies
- Code is clean and well-commented

---

## 🎯 READY TO EXECUTE

**Build this agent EXACTLY as specified.** Follow all requirements. Test thoroughly. The agent must survive all scenarios and score >0.

**REMEMBER:** You're building a restaurant, not optimizing a spreadsheet. Focus on customer experience, operational reliability, and financial health.