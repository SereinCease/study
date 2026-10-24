def discount(amount, rate):
    """Compute a discounted price. Negative amounts are rejected."""
    if amount < 0:
        raise ValueError('amount must be nonnegative')
    return amount * (1-rate)
