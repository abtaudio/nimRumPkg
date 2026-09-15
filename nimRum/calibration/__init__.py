"""nimRum calibration module.

Provides speaker measurement and auto-EQ fitting using UMIK-1.
"""

from nimRum.calibration.nimRumCalibrationEngine import (
    CalibrationEngine,
    MeasurementResult,
    TransferFunction,
)
from nimRum.calibration.nimRumCalibrationProfile import (
    CalibrationProfile,
    EQBand,
)
from nimRum.calibration.nimRumCalibrationDeploy import (
    compute_vol_adjustments,
    compute_vol_adjustments_detailed,
    deploy_calibration_profile,
    deploy_levels_to_tx_config,
    find_tx_config_path,
)

__all__ = [
    'CalibrationEngine',
    'CalibrationProfile',
    'EQBand',
    'MeasurementResult',
    'TransferFunction',
    'compute_vol_adjustments',
    'compute_vol_adjustments_detailed',
    'deploy_calibration_profile',
    'deploy_levels_to_tx_config',
    'find_tx_config_path',
]
