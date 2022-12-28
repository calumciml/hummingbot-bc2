import asyncio
import logging
from decimal import Decimal
from typing import Dict, Optional

import aiohttp

from hummingbot.core.network_base import NetworkBase
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger


class MarketMakingParamsDataFeed(NetworkBase):
    cadf_logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls.cadf_logger is None:
            cls.cadf_logger = logging.getLogger(__name__)
        return cls.cadf_logger

    def __init__(self, api_url, trading_pair, update_interval: float = 0.5):
        super().__init__()
        self._ready_event = asyncio.Event()
        self._shared_client: Optional[aiohttp.ClientSession] = None
        self._api_url = api_url
        self._trading_pair = trading_pair
        self._check_network_interval = 30.0
        self._ev_loop = asyncio.get_event_loop()
        self._bid_ask_markets_dict: Dict = {}
        self._prices_dict: Dict = {}
        self._update_interval: float = update_interval
        self._fetch_bid_ask_task: Optional[asyncio.Task] = None
        self._fetch_price_task: Optional[asyncio.Task] = None

    @property
    def name(self):
        return "price_api"

    @property
    def health_check_endpoint(self):
        return self._api_url

    def _http_client(self) -> aiohttp.ClientSession:
        if self._shared_client is None:
            self._shared_client = aiohttp.ClientSession()
        return self._shared_client

    async def check_network(self) -> NetworkStatus:
        # client = self._http_client()
        # async with client.request("GET", self.health_check_endpoint) as resp:
            # status_dict = await resp.json()
            # if resp.status != 200 or status_dict["status"] != "ONLINE":
                # raise Exception(f"Market Making API Feed {self.name} server error: {status_dict}")
        return NetworkStatus.CONNECTED

    def get_price(self, trading_pair) -> Decimal:
        return self.prices_dict.get(trading_pair)

    @property
    def prices_dict(self):
        return self._prices_dict

    @property
    def bid_ask_markets_json(self):
        return self._bid_ask_markets_dict

    async def fetch_price_loop(self):
        while True:
            try:
                await self.fetch_price()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger().network(f"Error fetching a new price from {self._api_url}.", exc_info=True,
                                      app_warning_msg=f"Couldn't fetch newest price from Market Making API.  {e}"
                                                      "Check network connection.")

            await asyncio.sleep(self._update_interval)

    async def fetch_bid_ask_loop(self):
        while True:
            try:
                await self.fetch_bid_ask()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().network(f"Error fetching a new bid-ask from {self._api_url}.", exc_info=True,
                                      app_warning_msg="Couldn't fetch newest price from Market Making API. "
                                                      "Check network connection.")

            await asyncio.sleep(self._update_interval)

    async def fetch_price(self):
        client = self._http_client()
        url = self._api_url + "/price/" + "?markets=" + self._trading_pair
        async with client.request("GET", url) as resp:
            resp_dict = await resp.json()
            if resp.status != 200:
                raise Exception(f"Custom API Feed {self.name} server error: {resp_dict}")
            self._prices_dict = resp_dict
        self._ready_event.set()

    async def fetch_bid_ask(self):
        client = self._http_client()
        url = self._api_url + self._trading_pair
        # self.logger().network(f"Fetching a new price from {url}.", exc_info=True,
                              # app_warning_msg=f"Fetch newest price from Market Making API {url}. "
                                              # "Fetching.")
        async with client.request("GET", url) as resp:
            resp_dict = await resp.text()
            if resp.status != 200:
                raise Exception(f"Custom API Feed {self.name} server error: {resp_dict}")
            self._bid_ask_markets_dict = resp_dict
        self._ready_event.set()

    async def start_network(self):
        await self.stop_network()
        # self._fetch_price_task = safe_ensure_future(self.fetch_price_loop())
        self._fetch_bid_ask_task = safe_ensure_future(self.fetch_bid_ask_loop())

    async def stop_network(self):
        if self._fetch_price_task is not None:
            self._fetch_price_task.cancel()
            self._fetch_price_task = None
        if self._fetch_bid_ask_task is not None:
            self._fetch_bid_ask_task.cancel()
            self._fetch_bid_ask_task = None

    def start(self):
        NetworkBase.start(self)

    def stop(self):
        NetworkBase.stop(self)