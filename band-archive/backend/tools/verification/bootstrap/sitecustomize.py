"""Verification-only bootstrap: suppress dotenv file discovery and reads."""
import os

os.environ['PYTHON_DOTENV_DISABLED'] = '1'

import dotenv
import dotenv.main


def _disabled(*args, **kwargs):
    return False


def _empty(*args, **kwargs):
    return {}


def _not_found(*args, **kwargs):
    return ''


dotenv.load_dotenv = dotenv.main.load_dotenv = _disabled
dotenv.dotenv_values = dotenv.main.dotenv_values = _empty
dotenv.find_dotenv = dotenv.main.find_dotenv = _not_found
dotenv.main.DotEnv.dict = _empty
dotenv.main.DotEnv.set_as_environment_variables = _disabled
os.environ['ANYTHING_VERIFICATION_BOOTSTRAP_READY'] = '1'
