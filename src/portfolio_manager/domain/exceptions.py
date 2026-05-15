class PortfolioError(Exception):
    """Base error for the portfolio manager."""


class ConfigError(PortfolioError):
    pass


class ProviderError(PortfolioError):
    pass


class FXRateUnavailable(ProviderError):
    pass


class PriceUnavailable(ProviderError):
    pass


class NotFoundError(PortfolioError):
    pass


class ValidationError(PortfolioError):
    pass
