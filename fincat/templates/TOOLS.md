# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## Financial Tools

### execute_trade

Execute a stock trade. **Requires HITL confirmation** before execution.

- `symbol`: Stock ticker (e.g., AAPL, 600519)
- `action`: BUY or SELL
- `quantity`: Number of shares
- `price`: Limit price (optional, uses market price if omitted)

### get_position

Query current holdings for a symbol or all positions.

### get_quote

Get real-time quote for a stock symbol.

### transfer_funds

Transfer funds between account and bank. **Requires HITL confirmation**.

### get_account

Query account information (balance, buying power, etc.).

### cancel_order

Cancel a pending order by order ID.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## glob — File Discovery

- Use `glob` to find files by pattern before falling back to shell commands
- Simple patterns like `*.py` match recursively by filename
- Use `entry_type="dirs"` when you need matching directories instead of files
- Use `head_limit` and `offset` to page through large result sets
- Prefer this over `exec` when you only need file paths

## grep — Content Search

- Use `grep` to search file contents inside the workspace
- Default behavior returns only matching file paths (`output_mode="files_with_matches"`)
- Supports optional `glob` filtering plus `context_before` / `context_after`
- Supports `type="py"`, `type="ts"`, `type="md"` and similar shorthand filters
- Use `fixed_strings=true` for literal keywords containing regex characters
- Use `output_mode="files_with_matches"` to get only matching file paths
- Use `output_mode="count"` to size a search before reading full matches
- Use `head_limit` and `offset` to page across large result sets
- Prefer this over `exec` for code and history searches
- Binary or oversized files may be skipped to keep results readable

## cron — Scheduled Reminders

- Please refer to cron skill for usage.

## Financial Calculations (6 tools)

All rates are decimals (0.05 = 5%). Returns JSON.

### valuation_calc — Investment Valuation
- `capm`: Cost of equity via CAPM
- `wacc`: Weighted average cost of capital
- `dcf`: Discounted cash flow valuation (returns EV, equity value, per-share value)
- `cagr`: Compound annual growth rate
- `percentile_rank`: Rank a value within a distribution

### loan_calc — Loan & Mortgage
- `loan_payment`: Monthly payment (equal-payment loan)
- `amortization_schedule`: Full repayment schedule
- `equal_principal_payment`: First/last payment (equal-principal loan)
- `equal_principal_schedule`: Equal-principal repayment schedule

### tvm_calc — Time Value of Money
- `compound_interest`: Future value with optional periodic additions
- `annuity_fv`: Future value of regular payments
- `annuity_pv`: Present value of regular payments

### bond_calc — Bond / Fixed Income
- `bond_price`: Price from yield
- `bond_ytm`: Yield-to-maturity from price (Newton-Raphson solver)

### budgeting_calc — Capital Budgeting
- `irr`: Internal rate of return (Newton-Raphson solver)
- `npv`: Net present value of cash flows

### ratio_calc — Financial Ratios
- `ratio_analysis`: Current/quick ratio, debt-to-equity, cash ratio with auto-judgment
