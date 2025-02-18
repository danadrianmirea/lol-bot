"""
Controls the League Client and continually starts League of Legends games.
"""

import logging
import multiprocessing as mp
import os
import random
import shutil
import traceback
from datetime import datetime, timedelta
from time import sleep

import pyautogui

from lolbot.bot import game, launcher, logger, window
from lolbot.common import accounts, config, proc
from lolbot.lcu.lcu_api import LCUApi, LCUError

log = logging.getLogger(__name__)

# Click Ratios
POST_GAME_OK_RATIO = (0.4996, 0.9397)
POST_GAME_SELECT_CHAMP_RATIO = (0.4977, 0.5333)
POPUP_SEND_EMAIL_X_RATIO = (0.6960, 0.1238)

# Errors
MAX_BOT_ERRORS = 5
MAX_PHASE_ERRORS = 20


class BotError(Exception):
    """Indicates the League Client instance should be restarted."""
    pass


class Bot:
    """Handles the League Client and all tasks needed to start a new game."""
    def __init__(self) -> None:
        self.api = LCUApi()
        self.config = config.load_config()
        self.league_dir = self.config['league_dir']
        self.max_level = self.config['max_level']
        self.lobby = self.config['lobby']
        self.champs = self.config['champs']
        self.dialog = self.config['dialog']
        self.account = None
        self.phase = None
        self.prev_phase = None
        self.bot_errors = 0
        self.phase_errors = 0
        self.game_errors = 0

    def run(self, message_queue: mp.Queue, games: mp.Value, errors: mp.Value) -> None:
        """Main loop, gets an account, launches league, monitors account level, and repeats."""
        logger.MultiProcessLogHandler(message_queue).set_logs()
        self.api.update_auth_timer()
        self.print_ascii()
        # self.wait_for_patching()
        self.set_game_config()
        while True:
            try:
                errors.value = self.bot_errors
                self.account = accounts.get_account(self.max_level)
                launcher.launch_league(self.account['username'], self.account['password'])
                self.leveling_loop(games)
                proc.close_all_processes()
                self.bot_errors = 0
                self.phase_errors = 0
                self.game_errors = 0
            except BotError as be:
                log.error(be)
                self.bot_errors += 1
                self.phase_errors = 0
                self.game_errors = 0
                if self.bot_errors == MAX_BOT_ERRORS:
                    log.error("Max errors reached. Exiting")
                    return
                else:
                    proc.close_all_processes()
            except launcher.LaunchError as le:
                log.error(le)
                log.error("Launcher Error. Exiting")
                return
            except Exception as e:
                log.error(e)
                if traceback.format_exc() is not None:
                    log.error(traceback.format_exc())
                log.error("Unknown Error. Exiting")
                return

    def leveling_loop(self, games: mp.Value) -> None:
        """Loop that takes action based on the phase of the League Client, continuously starts games."""
        while not self.account_leveled():
            match self.get_phase():
                case 'None' | 'Lobby':
                    self.start_matchmaking()
                case 'Matchmaking':
                    self.queue()
                case 'ReadyCheck':
                    self.accept_match()
                case 'ChampSelect':
                    self.champ_select()
                case 'InProgress':
                    game.play_game()
                case 'Reconnect':
                    self.reconnect()
                case 'WaitingForStats':
                    self.wait_for_stats()
                case 'PreEndOfGame':
                    self.pre_end_of_game()
                case 'EndOfGame':
                    self.end_of_game()
                    games.value += 1
                case _:
                    raise BotError("Unknown phase. {}".format(self.phase))

    def get_phase(self) -> str:
        """Requests the League Client phase."""
        err = None
        for i in range(15):
            try:
                self.prev_phase = self.phase
                self.phase = self.api.get_phase()
                if self.prev_phase == self.phase and self.phase != "Matchmaking" and self.phase != 'ReadyCheck':
                    self.phase_errors += 1
                    if self.phase_errors == MAX_PHASE_ERRORS:
                        raise BotError("Transition error. Phase will not change")
                else:
                    self.phase_errors = 0
                sleep(1.5)
                return self.phase
            except LCUError as e:
                err = e
        raise BotError(f"Could not get phase: {err}")

    def start_matchmaking(self) -> None:
        """Starts matchmaking for a particular game mode, will also wait out dodge timers."""
        # Create lobby
        lobby_name = ""
        for lobby, lid in config.ALL_LOBBIES.items():
            if lid == self.lobby:
                lobby_name = lobby + " "
        log.info(f"Creating {lobby_name.lower()}lobby")
        try:
            self.api.create_lobby(self.lobby)
            sleep(3)
        except LCUError:
            return

        # Start Matchmaking
        log.info("Starting queue")
        try:
            self.api.start_matchmaking()
            sleep(1)
        except LCUError:
            return

        # Wait out dodge timer
        try:
            time_remaining = self.api.get_dodge_timer()
            if time_remaining > 0:
                log.info(f"Dodge Timer. Time Remaining: {time_remaining}")
                sleep(time_remaining)
        except LCUError:
            return

        # TODO when queue times get too high switch to pvp lobby, start it, and then switch back

    def queue(self) -> None:
        """Waits until the League Client Phase changes to something other than 'Matchmaking'."""
        log.info("Waiting for Ready Check")
        start = datetime.now()
        while True:
            try:
                if self.api.get_phase() != 'Matchmaking':
                    return
                elif datetime.now() - start > timedelta(minutes=15):
                    raise BotError("Queue Timeout")
                sleep(1)
            except LCUError:
                sleep(1)

    def accept_match(self) -> None:
        """Accepts the Ready Check."""
        try:
            if self.prev_phase != "ReadyCheck":
                log.info("Accepting Match")
            self.api.accept_match()
        except LCUError:
            pass

    def champ_select(self) -> None:
        """Handles the Champ Select Lobby."""
        log.info("Locking in champ")
        champ_index = -1
        while True:
            try:
                data = self.api.get_champ_select_data()
                champ_list = self.champs + self.api.get_available_champion_ids()
            except LCUError:
                return
            try:
                for action in data['actions'][0]:
                    if action['actorCellId'] == data['localPlayerCellId']:
                        if action['championId'] == 0:  # No champ hovered. Hover a champion.
                            champ_index += 1
                            self.api.hover_champion(action['id'], champ_list[champ_index])
                        elif not action['completed']:  # Champ is hovered but not locked in.
                            self.api.lock_in_champion(action['id'], action['championId'])
                        else:  # Champ is locked in. Nothing left to do.
                            sleep(2)
            except LCUError:
                pass

    def reconnect(self) -> None:
        """Attempts to reconnect to an ongoing League of Legends match."""
        log.info("Reconnecting to game")
        for i in range(3):
            try:
                self.api.game_reconnect()
                sleep(3)
                return
            except LCUError:
                sleep(2)
        log.warning('Could not reconnect to game')

    def wait_for_stats(self) -> None:
        """Waits for the League Client Phase to change to something other than 'WaitingForStats'."""
        log.info("Waiting for stats")
        for i in range(60):
            sleep(2)
            try:
                if self.api.get_phase() != 'WaitingForStats':
                    return
            except LCUError:
                pass
        raise BotError("Waiting for stats timeout")

    def pre_end_of_game(self) -> None:
        """Handles league of legends client reopening after a game, honoring teammates, and clearing level-up/mission rewards."""
        log.info("Honoring teammates and accepting rewards")
        sleep(3)
        try:
            proc.click(POPUP_SEND_EMAIL_X_RATIO, proc.LEAGUE_CLIENT_WINNAME, 2)
            if not self.honor_player():
                sleep(60)  # Honor failed for some reason, wait out the honor screen
            proc.click(POPUP_SEND_EMAIL_X_RATIO, proc.LEAGUE_CLIENT_WINNAME, 2)
            for i in range(3):
                proc.click(POST_GAME_SELECT_CHAMP_RATIO, proc.LEAGUE_CLIENT_WINNAME, 1)
                proc.click(POST_GAME_OK_RATIO, proc.LEAGUE_CLIENT_WINNAME, 1)
            proc.click(POPUP_SEND_EMAIL_X_RATIO, proc.LEAGUE_CLIENT_WINNAME, 1)
        except (window.WindowNotFound, pyautogui.FailSafeException):
            sleep(3)

    def honor_player(self) -> bool:
        """Honors a player in the post game lobby. There are no enemies to honor in bot lobbies."""
        for i in range(3):
            try:
                players = self.api.get_players_to_honor()
                index = random.randint(0, len(players) - 1)
                self.api.honor_player(players[index]['summonerId'])
                sleep(2)
                return True
            except LCUError as e:
                log.warning(e)
        log.warning('Honor Failure')
        return False

    def end_of_game(self) -> None:
        """Transitions out of EndOfGame."""
        log.info("Getting back into queue")
        posted = False
        for i in range(15):
            try:
                if self.api.get_phase() != 'EndOfGame':
                    return
                if not posted:
                    self.api.play_again()
                else:
                    self.start_matchmaking()
                posted = not posted
                sleep(1)
            except LCUError:
                pass
        raise BotError("Could not exit play-again screen")

    def account_leveled(self) -> bool:
        """Checks if account has reached max level."""
        try:
            if self.api.get_summoner_level() >= self.max_level:
                if self.account['username'] == self.api.get_display_name():
                    self.account['level'] = self.max_level
                    accounts.save_or_add(self.account)
                log.info("Account successfully leveled")
                return True
            return False
        except LCUError:
            return False

    def wait_for_patching(self) -> None:
        """Checks if the League Client is patching and waits till it is finished."""
        log.info("Checking for Client Updates")
        logged = False
        while self.api.is_client_patching():
            if not logged:
                log.info("Client is patching...")
                logged = True
            sleep(3)
        log.info("Client is up to date")

    def set_game_config(self) -> None:
        """Overwrites the League of Legends game config."""
        log.info("Overwriting game configs")
        path = self.league_dir + '/Config/game.cfg'
        folder = os.path.abspath(os.path.join(path, os.pardir))
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
            except Exception as e:
                log.error('Failed to delete %s. Reason: %s' % (file_path, e))
        shutil.copy(proc.resource_path(config.GAME_CFG), path)

    @staticmethod
    def print_ascii() -> None:
        """Prints some League ascii art."""
        print("""\n\n            
                    ──────▄▌▐▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▌
                    ───▄▄██▌█ BEEP BEEP
                    ▄▄▄▌▐██▌█ -15 LP DELIVERY
                    ███████▌█▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▌
                    ▀(⊙)▀▀▀▀▀▀▀(⊙)(⊙)▀▀▀▀▀▀▀▀▀▀(⊙)\n\n\t\t\t\tLoL Bot\n\n""")
