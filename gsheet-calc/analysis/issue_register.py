"""Issue register for collecting and managing ReviewIssues across all phases."""

from models.issue import ReviewIssue


class IssueRegister:
    """Collects, deduplicates, and sorts ReviewIssues from all pipeline stages."""

    SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    def __init__(self) -> None:
        self._issues: dict[str, ReviewIssue] = {}

    def add(self, issue: ReviewIssue) -> None:
        """Add an issue. If the ID already exists, keep the higher severity."""
        existing = self._issues.get(issue.id)
        if existing:
            if self.SEVERITY_ORDER.get(issue.severity, 9) < self.SEVERITY_ORDER.get(
                existing.severity, 9
            ):
                self._issues[issue.id] = issue
        else:
            self._issues[issue.id] = issue

    def add_all(self, issues: list[ReviewIssue]) -> None:
        """Add a batch of issues."""
        for issue in issues:
            self.add(issue)

    def get_all(self) -> list[ReviewIssue]:
        """Return all issues sorted by severity (critical first)."""
        return sorted(
            self._issues.values(),
            key=lambda i: self.SEVERITY_ORDER.get(i.severity, 9),
        )

    def get_blocking(self) -> list[ReviewIssue]:
        """Return only blocking issues."""
        return [i for i in self.get_all() if i.blocking]

    def has_blocking(self) -> bool:
        """Check if any blocking issues exist."""
        return any(i.blocking for i in self._issues.values())

    def resolve(self, issue_id: str, status: str = "resolved") -> None:
        """Mark an issue as resolved or assumed."""
        if issue_id in self._issues:
            self._issues[issue_id].status = status

    @property
    def count(self) -> int:
        return len(self._issues)
