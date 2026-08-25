"""Research-only projection code kept separate from the daily screener."""

from .calibration import ResidualArtifact, load_calibration_artifact
from .models import MODEL_VERSION, ShadowProjection
from .projection import ProjectionConfig, project_points_line

__all__ = [
    "MODEL_VERSION",
    "ProjectionConfig",
    "ResidualArtifact",
    "ShadowProjection",
    "load_calibration_artifact",
    "project_points_line",
]
