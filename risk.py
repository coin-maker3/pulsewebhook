class RiskManager:
    RISK_PER_TRADE = 0.05        # 5% of portfolio per trade
    MAX_POSITION_SIZE = 0.40     # Maximum 40% of portfolio in one trade
    MAX_TRADES_PER_DAY = 10
    DAILY_LOSS_LIMIT = 0.10      # Stop trading if down 10% on the day

    def calculate_position_size(self, portfolio_value: float, entry_price: float, stop_price: float) -> int:
        risk_amount = portfolio_value * self.RISK_PER_TRADE
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return 0
        shares = int(risk_amount / risk_per_share)
        max_shares_by_position = int((portfolio_value * self.MAX_POSITION_SIZE) / entry_price)
        return min(shares, max_shares_by_position)

    def can_trade(self, trades_today: int, daily_pnl: float, portfolio_value: float) -> tuple[bool, str]:
        if trades_today >= self.MAX_TRADES_PER_DAY:
            return False, "Daily trade limit reached"
        if daily_pnl < -(portfolio_value * self.DAILY_LOSS_LIMIT):
            return False, "Daily loss limit reached"
        return True, "OK"
