from __future__ import annotations

import subprocess
import sys
import textwrap


def test_top_level_models_and_backend_models_import_separately() -> None:
    code = textwrap.dedent(
        """
        import backend.models
        import models.pure_jump_vol.signals

        assert backend.models.__name__ == "backend.models"
        assert models.pure_jump_vol.signals.__name__ == "models.pure_jump_vol.signals"
        """
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_sentiment_compatibility_imports_are_not_circular() -> None:
    code = textwrap.dedent(
        """
        from backend.nlp.sentiment import FinancialSentimentAnalyzer as BackendAnalyzer
        from nlp.sentiment import FinancialSentimentAnalyzer as TopLevelAnalyzer

        assert BackendAnalyzer is TopLevelAnalyzer
        assert TopLevelAnalyzer().score("strong profit growth")["label"] == "Bullish"
        """
    )
    subprocess.run([sys.executable, "-c", code], check=True)
