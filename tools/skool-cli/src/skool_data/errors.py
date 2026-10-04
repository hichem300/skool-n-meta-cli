class SkoolDataError(Exception):
    """Base error safe for CLI handling."""


class ConfigurationError(SkoolDataError):
    """Local configuration is missing or invalid."""


class AuthenticationError(SkoolDataError):
    """Skool rejected the imported credentials."""


class TransportError(SkoolDataError):
    """A request failed before a valid response was received."""


class SchemaError(SkoolDataError):
    """A Skool response did not have the expected shape."""


class StorageError(SkoolDataError):
    """Runtime data could not be safely persisted."""


class ExportError(SkoolDataError):
    """A requested export could not be safely written."""
