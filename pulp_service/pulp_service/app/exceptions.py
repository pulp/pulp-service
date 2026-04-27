from gettext import gettext as _

from pulpcore.plugin.exceptions import PulpException


class VulnReportTaskError(PulpException):
    """
    Raised when the background vulnerability report thread fails.
    """

    error_code = "SVC0001"

    def __init__(self, osv_data):
        self.osv_data = osv_data

    def __str__(self):
        return f"[{self.error_code}] " + _(
            "Background vuln report task failed to execute: {osv_data}"
        ).format(osv_data=self.osv_data)


class VulnReportThreadDiedError(PulpException):
    """
    Raised when the vulnerability report background thread dies unexpectedly.
    """

    error_code = "SVC0002"

    def __str__(self):
        return f"[{self.error_code}] " + _("Vuln report task thread died unexpectedly.")


class VulnReportThreadTimeoutError(PulpException):
    """
    Raised when the vulnerability report background thread takes too long.
    """

    error_code = "SVC0003"

    def __str__(self):
        return f"[{self.error_code}] " + _("Background vuln report thread took too long.")


class UnsupportedPackageTypeError(PulpException):
    """
    Raised when a content type cannot be mapped to a supported package ecosystem.
    """

    error_code = "SVC0004"

    def __str__(self):
        return f"[{self.error_code}] " + _("Package type not supported!")
