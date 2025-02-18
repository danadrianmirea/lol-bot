"""
Handles multi-platform creating/writing LoLBot's configurations to json file.
"""

import os
import json

if os.name == 'nt':
    CONFIG_DIR = os.path.join(os.getenv('LOCALAPPDATA'), 'LoLBot')
else:
    CONFIG_DIR = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'LoLBot')

os.makedirs(CONFIG_DIR, exist_ok=True)

BAK_DIR = os.path.join(CONFIG_DIR, 'bak')
LOG_DIR = os.path.join(CONFIG_DIR, 'logs')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
ACCOUNT_PATH = os.path.join(CONFIG_DIR, 'accounts.json')

GAME_CFG = 'lolbot/resources/game.cfg'

ALL_LOBBIES = {
    'Draft Pick': 400,
    'Ranked Solo/Duo': 420,
    'Blind Pick': 430,
    'Ranked Flex': 440,
    'ARAM': 450,
    'Intro Bots': 870,
    'Beginner Bots': 880,
    'Intermediate Bots': 890,
    'Normal TFT': 1090,
    'Ranked TFT': 1100,
    'Hyper Roll TFT': 1130,
    'Double Up TFT': 1160
}

BOT_LOBBIES = {
    'Intro Bots': 870,
    'Beginner Bots': 880,
    'Intermediate Bots': 890,
}


def load_config() -> dict:
    """Load configuration from disk or set defaults"""
    default_config = {
        'league_dir': 'C:/Riot Games/League of Legends',
        'lobby': 880,
        'max_level': 30,
        'champs': [21, 18, 22, 67],
        'dialog': ["mid ples", "plannin on goin mid team", "mid por favor", "bienvenidos, mid", "howdy, mid", "goin mid", "mid"]
    }
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'w') as configfile:
            json.dump(default_config, configfile, indent=4)
        return default_config
    else:
        try:
            with open(CONFIG_PATH, 'r') as configfile:
                return json.load(configfile)
        except json.JSONDecodeError:
            return default_config


def save_config(config) -> None:
    """Save the configuration to disk"""
    with open(CONFIG_PATH, 'w') as configfile:
        json.dump(config, configfile, indent=4)
