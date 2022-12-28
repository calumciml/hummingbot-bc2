import copy
import json
import logging
import time
from decimal import Decimal
from typing import List

from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.data_feed.market_making_api_feed import MarketMakingParamsDataFeed
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase

"""
    Statistical Market Making on Multiple Trading Pairs with External Bid and Ask Source
    
    WARNINGS
        1.  We are not checking the exchange wallet at startup.  When wallet 
            is broken, the price is likely to be lower than other exchanges 
            and the bot is likely to buy a token that you don't want to hold 
            on this exchange.
        2.  We do not check for stale reference data.  The reference feed must 
            do this and return a zero or negative price.
    
    Purpose of Script
        *   Do market making on many small, low liquidity markets.
        *   Use a reference feed with bid and ask, to buy at a discount and 
            sell at a premium.
        *   Be efficient with exchange API limits (don’t be too aggressive 
            with order cancel and replace)
        *   This is for unhedged trading.  Market risk is accepted.
        *   Your reference feed can be whatever you make it to be.  Just provide 
            Json output with currency pairs (QUOTE-BID format), each with a 
            bid value and an ask value.
    Structure
        *   We loop trading pairs three times:
            *   Part 1: Cancel unprofitable orders
            *   Part 2: Cancel uncompetitive orders
            *   Part 3: Place new orders
"""


class SimplePMM(ScriptStrategyBase):

    """
    Set ratios and distances (? means buy or sell):
        ?_place_ratio:
            Minimum % distance from reference price, to place an order.
            Set higher than buy_cancel_unprofitable_ratio to save on API activity.
        ?_place_max_distance:
            Maximum % distance from top of book, to place an order.
            Set lower than buy_cancel_outbid_ratio to save on API activity.
        ?_cancel_unprofitable_ratio:
            Minimum % distance from reference price, to keep an order.
        ?_cancel_outbid_ratio:
            Maximum % distance from top of book, to keep an order.
    """
    buy_place_ratio = 0.0050
    buy_place_max_distance = 0.02
    buy_cancel_unprofitable_ratio = 0.0025
    buy_cancel_outbid_ratio = 0.0300

    sell_place_ratio = 0.0005
    sell_place_max_distance = 0.02
    sell_cancel_unprofitable_ratio = -0.0005
    sell_cancel_outbid_ratio = 0.0300

    """
    Thsi delay avoids placing an order while an equivalent order is in progress.
    """
    time_between_orders_for_trading_pair = 5

    """
    Define trading exchange and pairs.
    This script supports a single exchange and multiple pairs.
    
    Default exchange bittrex with some very low liquidity markets to demonstrate 
    the lack of API traffic when this script is configured well.

    Default many pairs to demonstrate that Hummingbot is coping well.
    This might not be the case for high volume markets.
    """
    exchange = "bittrex"
    trading_pairs = {
        "DUSK-BTC", "QLC-BTC", "NXS-BTC", "CND-BTC", "PIVX-BTC", "MTL-BTC", "NAV-BTC", "VIA-BTC", "ADX-BTC", 
        "GO-BTC", "HIVE-BTC", "KMD-BTC", "LSK-BTC", "NMR-BTC", "POWR-BTC", "MANA-BTC", "ICX-BTC", "KLAY-BTC", "STRAX-BTC", 
        "ARK-BTC", "GRS-BTC", "DOT-BTC", "SNT-BTC", "STEEM-BTC", "BTS-BTC", "UTK-BTC", "STORJ-BTC", "IOST-BTC", "OMG-BTC", 
        "BAT-BTC", "KSM-BTC", "ETC-BTC", "AKRO-BTC", "DASH-BTC", "BAL-BTC", "NKN-BTC", "WAVES-BTC", "FIL-BTC", "QNT-BTC", 
        "AR-BTC", "SXP-BTC", "ARDR-BTC", "ONG-BTC", "SYS-BTC", "DCR-BTC", "LRC-BTC", "GLM-BTC", "DOGE-BTC", "XLM-BTC", 
        "LINK-BTC", "MATIC-BTC", "SC-BTC", "CVC-BTC", "ZEC-BTC", "PUNDIX-BTC", "XEM-BTC", "QTUM-BTC", "XVG-BTC", "RLC-BTC", 
        "ZRX-BTC", "ATOM-BTC", "AVAX-BTC", "EOS-BTC", "CRV-BTC", "DAI-BTC", "GNO-BTC", "DGB-BTC", "DNT-BTC", "LOOM-BTC", 
        "XRP-BTC", "LTC-BTC", "ADA-BTC", "BCH-BTC", "1INCH-BTC", "ANKR-BTC", "ANT-BTC", "BNT-BTC", "CELO-BTC", 
        "RVN-BTC", "STMX-BTC", "VET-BTC", "UMA-BTC", "WAXP-BTC", "MDT-BTC", "SOL-BTC", "MFT-BTC", "MKR-BTC", "TRX-BTC", 
        "VITE-BTC", "NEO-BTC", "WINGS-BTC", "XTZ-BTC", "RSR-BTC", "SAND-BTC", "OCEAN-BTC", "ZIL-BTC", "OGN-BTC", "SNX-BTC", 
        "ONT-BTC", "SUSHI-BTC", "OXT-BTC", "TUSD-BTC", "PLA-BTC", "UNI-BTC", "PROM-BTC", "PYR-BTC", "WBTC-BTC", "RAMP-BTC", 
        "ZEN-BTC", "HBAR-BTC", "AAVE-BTC", "IOTX-BTC", "ALGO-BTC", "AMP-BTC", "ENJ-BTC", "KDA-BTC", "BAND-BTC", "ENG-BTC", 
        "BIFI-BTC", "BSV-BTC", "GRT-BTC", "CKB-BTC", "COMP-BTC", "KNC-BTC",

        "SNX-ETH", "NMR-ETH", "OMG-ETH", "SNT-ETH", "ADA-ETH", "HBAR-ETH", "CELO-ETH", "XEM-ETH", "ADX-ETH", "POWR-ETH", 
        "MANA-ETH", "STRAX-ETH", "DOT-ETH", "DASH-ETH", "ETC-ETH", "WAVES-ETH", "FIL-ETH", "CVC-ETH", "GLM-ETH", "GNO-ETH", 
        "ATOM-ETH", "BAT-ETH", "DOGE-ETH", "PUNDIX-ETH", "EOS-ETH", "MATIC-ETH", "AAVE-ETH", "AMP-ETH", "LINK-ETH", "LTC-ETH", 
        "QTUM-ETH", "TRX-ETH", "XLM-ETH", "XRP-ETH", "XVG-ETH", "ZEC-ETH", "ZRX-ETH", "1INCH-ETH", "ANKR-ETH", "ANT-ETH", 
        "AVAX-ETH", "BAL-ETH", "BAND-ETH", "BCH-ETH", "BNT-ETH", "BSV-ETH", "COMP-ETH", "CRV-ETH", "DAI-ETH", 
        "DGB-ETH", "ENG-ETH", "ENJ-ETH", "GRT-ETH", "JASMY-ETH", "KNC-ETH", "KSM-ETH", "MKR-ETH", "NEO-ETH", "OGN-ETH", 
        "QNT-ETH", "RAMP-ETH", "RVN-ETH", "SC-ETH", "STMX-ETH", "SUSHI-ETH", "TUSD-ETH", "UMA-ETH", "UNI-ETH", 
        "VET-ETH", "WAXP-ETH", "WBTC-ETH", "XTZ-ETH", "IRIS-BTC"
        }
    
    markets = {
        exchange: trading_pairs
    }

    """
    Define Quote Currencies
        size_in_quote_units: Some small value is suggested, roughly consistent across quote currencies.
    """
    quote_curs = {
        "USD": {
            "size_in_quote_units": 5.0
        },
        "USDT": {
            "size_in_quote_units": 5.0
        },
        "BTC": {
            "size_in_quote_units": 0.000138
        },
        "ETH": {
            "size_in_quote_units": 0.00178
        }
    }

    """
    Sizing:
        size_multiple: To adjust the above sizes (e.g. 3 * size_in_quote_units or 3 * size_in_quote_minimum)
    """
    size_multiple = 1.10

    """
    Define your reference feed URL and trading pair delimiter.

    IMPORTANT:
        The sample feed below is NOT production ready.
        It might disappear at any time.
        It might crash unexpectedly.
        It might provide bad data.

    """
    trading_pairs_string = ("_".join(trading_pairs))
    market_making_api = MarketMakingParamsDataFeed(api_url="http://api-test-33.nowcaster.io/bid-ask/?markets=", trading_pair=trading_pairs_string)

    """
    Track the last cancel and order time.  This could be used to help balance order API limit across instances.
    """
    last_cancel_time = {}
    for trading_pair in trading_pairs:
        last_cancel_time[trading_pair] = 0
    last_order_time = {}
    for trading_pair in trading_pairs:
        last_order_time[trading_pair] = time.time()

    """
    Create open orders dictionary.
    """
    orders = {}
    for trading_pair in trading_pairs:
        orders[trading_pair] = {}
        for is_buy in [1,0]:
            orders[trading_pair][is_buy] = {}

    """
    Create orderbooks dictionary.
    """
    for trading_pair in trading_pairs:
        orderbook_bid = {}
        orderbook_ask = {}

    def on_tick(self):
        cancels_in_progress = 0
    
        """
        Do nothing if trading exchange is not initialised.
        """
        if not self.market_making_api.started:
            self.market_making_api.start()
            return []

        """
        Fetch reference prices.
        """
        
        try:
            self.bid_ask_markets_dict = json.loads(self.market_making_api.bid_ask_markets_json)
        except TypeError as e:
            self.logger().info(f"TypeError.")
            self.logger().info(f"Exception {e}")
            return([])

        """
        Fetch open orders once and store.
        Note: This was to reduces hits to get_active_orders().  Possibly not necessary.
        """
        self.connector = self.connectors[self.exchange]
        for trading_pair in self.trading_pairs:
            for is_buy in [1,0]:
                self.orders[trading_pair][is_buy] = {}
        for order in self.get_active_orders(connector_name=self.exchange) :
            self.orders[order.trading_pair][order.is_buy][order.client_order_id] = order

        """
        Fetch orderbooks once and store.
        Note: This was to reduce hits to get_price().  Possibly not necessary.
        """
        for trading_pair in self.trading_pairs:
            self.orderbook_bid[trading_pair] = -5
            self.orderbook_ask[trading_pair] = -5
            self.orderbook_bid[trading_pair] = self.connector.get_price(trading_pair, False)
            self.orderbook_ask[trading_pair] = self.connector.get_price(trading_pair, True)
        
        """
        Fetch balances once and store.
        Note: This was to reduce hits to get_balance_df().  Possibly not necessary.
        """
        balance_df = self.get_balance_df()[:]
        
        """
        ###################################################
        Part One: Cancel orders that would no longer be profitable due to a change in reference price.
        Loop trading pairs.
        """
        num_cancels_unprofitable = 0
        for trading_pair in self.trading_pairs:

            """
            Collect bid and ask prices from orderbook and reference feed.
            """
            self.trading_pair = trading_pair

            bid_ask_dict = self.bid_ask_markets_dict[trading_pair]
            orderbook_bid = self.orderbook_bid[trading_pair]
            orderbook_ask = self.orderbook_ask[trading_pair]

            ref_price_buy = bid_ask_dict["bid"]
            ref_price_sell = bid_ask_dict["ask"]
            
            """
            Skip if reference feed provides non-positive value (it should do this when the source data are stale).
            """
            if ref_price_buy <= 0 or ref_price_sell <= 0:
                continue
            
            """
            For buys and then sells, cancel unprofitable orders.
            """

            for client_order_id, order in self.orders[trading_pair][1].items():
                if order.price > (Decimal(ref_price_buy) * Decimal(1 - self.buy_cancel_unprofitable_ratio)):
                    self.cancel(self.exchange, trading_pair, order.client_order_id)
                    self.logger().info(f"Cancel,unprofitable,trading_pair,{trading_pair},buy,{order.client_order_id},order_price,{order.price },>,ref_price,{Decimal(ref_price_buy)},ref_price-ratio,{(Decimal(ref_price_buy) * Decimal(1 - self.buy_cancel_unprofitable_ratio))}")
                    num_cancels_unprofitable += 1
                    self.last_cancel_time[trading_pair] = time.time()
            for client_order_id, order in self.orders[trading_pair][0].items():
                if order.price < (Decimal(ref_price_sell) * Decimal(1 + self.sell_cancel_unprofitable_ratio)):
                    self.cancel(self.exchange, trading_pair, order.client_order_id)
                    self.logger().info(f"Cancel,unprofitable,trading_pair,{trading_pair},sell,{order.client_order_id},order_price,{order.price},<,ref_price,{Decimal(ref_price_sell)},ref_price+ratio,{(Decimal(ref_price_sell) * Decimal(1 + self.sell_cancel_unprofitable_ratio))}")
                    num_cancels_unprofitable += 1
                    self.last_cancel_time[trading_pair] = time.time()

        if num_cancels_unprofitable > 0:
            cancels_in_progress = 1

        """
        ###################################################
        Part Two: Cancel orders that have been outbid, where we could offer a better price.
        Loop trading pairs.
        """
        num_cancels_outbid = 0

        """
        Wait for next cycle if there were cancels due to unprofitable order prices.
        This prioritises exchange API usage.
        """
        if cancels_in_progress ==0:
            for trading_pair in self.trading_pairs:
            
                """
                Collect bid and ask prices from orderbook and reference feed.
                """
                self.trading_pair = trading_pair

                bid_ask_dict = self.bid_ask_markets_dict[trading_pair]
                ref_price_buy = bid_ask_dict["bid"]
                ref_price_sell = bid_ask_dict["ask"]

                orderbook_bid = self.orderbook_bid[trading_pair]
                orderbook_ask = self.orderbook_ask[trading_pair]

                """
                Skip if reference feed provides non-positive value (it should do this when the source data are stale).
                """
                if ref_price_buy <= 0 or ref_price_sell <= 0:
                    continue

                """
                For buys and then sells, cancel orders that have been outbid by too much.
                """

                for client_order_id, order in self.orders[trading_pair][1].items():
                    if order.price < (Decimal(orderbook_bid) * Decimal(1 - self.buy_cancel_outbid_ratio)):
                        self.cancel(self.exchange, trading_pair, order.client_order_id)
                        self.logger().info(f"Cancel,outbid,trading_pair,{trading_pair},buy,{order.client_order_id},order_price,{order.price},>,ref price,{Decimal(ref_price_buy)},ref price-ratio,{(Decimal(ref_price_buy) * Decimal(1 - self.buy_cancel_outbid_ratio))}")
                        num_cancels_outbid += 1
                        self.last_cancel_time[trading_pair] = time.time()
                for client_order_id, order in self.orders[trading_pair][0].items():
                    if order.price > (Decimal(orderbook_ask) * Decimal(1 + self.sell_cancel_outbid_ratio)):
                        self.cancel(self.exchange, trading_pair, order.client_order_id)
                        self.logger().info(f"Cancel,outbid,trading_pair,{trading_pair},  sell,{order.client_order_id},order_price,{order.price},<,ref price,{Decimal(ref_price_sell)}ref price+ratio,{(Decimal(ref_price_sell) * Decimal(1 + self.sell_cancel_outbid_ratio))}")
                        num_cancels_outbid += 1
                        self.last_cancel_time[trading_pair] = time.time()

        if num_cancels_outbid > 0:
            cancels_in_progress = 1

        """
        ###################################################
        Part Three: Propose new orders.
            If there is an existing buy (or sell if selling) order for the market, do nothing.
            If the new order would be too far from top of book, do nothing.
            If the new order will be above (or below if selling) the current top of book, place half way between target price and top of book.
            Else place the order at target price.
        """

        num_orders = 0
        skip_proposal_for_count = 0
        skip_proposal_for_time = 0
        skip_proposal_for_distance = 0
        skip_proposal_for_cancels = 0
        skip_proposal_for_balance = 0

        """
        Wait for next cycle if there were cancels due to unprofitable order prices.
        This prioritises exchange API usage.
        """
        if cancels_in_progress == 0:
            for trading_pair in self.trading_pairs:

                """
                Only allow one proposal per loop.  This helps to prioritise cancels.
                """
                if num_orders > 0:
                    skip_proposal_for_count += 1
                    continue

                """
                Empty any proposal from previous trading pair.
                """
                proposal: List[OrderCandidate] = []

                """
                Avoid placing an order while an equivalent order is in progress.
                """
                if time.time() < self.last_order_time[trading_pair] + self.time_between_orders_for_trading_pair:
                    skip_proposal_for_time += 1
                    continue
                
                """
                Collect bid and ask prices from orderbook and reference feed.
                """

                self.trading_pair = trading_pair

                bid_ask_dict = self.bid_ask_markets_dict[trading_pair]
                ref_price_buy = bid_ask_dict["bid"]
                ref_price_sell = bid_ask_dict["ask"]

                buy_price_max = Decimal(ref_price_buy) * Decimal(1 - self.buy_place_ratio)
                sell_price_min = Decimal(ref_price_sell) * Decimal(1 + self.sell_place_ratio)

                orderbook_bid = self.orderbook_bid[trading_pair]
                orderbook_ask = self.orderbook_ask[trading_pair]

                """
                Skip if reference price is non-float or NaN.
                """
                if (type(buy_price_max) == int or type(buy_price_max) == float or type(buy_price_max) == Decimal) and buy_price_max == buy_price_max:
                    pass
                else:
                    self.logger().info(f"Reference buy_price_max {trading_pair} {buy_price_max} type(buy_price_max) {type(buy_price_max)}")
                    continue
                if (type(sell_price_min) == int or type(sell_price_min) == float or type(sell_price_min) == Decimal) and sell_price_min == sell_price_min:
                    pass
                else:
                    self.logger().info(f"Reference sell_price_min {trading_pair} {sell_price_min} type(sell_price_min) {type(sell_price_min)}")
                    continue

                base_cur, quote_cur = trading_pair.split("-")
                
                buy_order = 0
                """
                Skip if we have an existing buy limit order for this trading pair.
                Skip if the trading pair is "IRIS-BTC", because we're not checking wallet status yet.
                Skip if reference price is too far from top of book.  Not placing the uncompetitive order helps with exchange API limits.
                """
                if len(self.orders[trading_pair][1]) == 0 and trading_pair != "IRIS-BTC":
                    if orderbook_bid > Decimal(buy_price_max) * Decimal(1 + self.buy_place_max_distance):
                        skip_proposal_for_distance += 1
                        pass
                    else:

                        """
                        If the order will be the new top of book, place it half way between reference price and old top of book +/- config spread
                        If it won’t be the new top of book, place it at the reference price +/- config spread
                        """
                        if orderbook_bid < buy_price_max:
                            buy_price = Decimal(buy_price_max + orderbook_bid) / 2
                        else:
                            buy_price = buy_price_max

                        """
                        Convert the size units and check available balance.
                        Place order if size looks OK.  
                        """
                        order_amount = Decimal(self.quote_curs[quote_cur]["size_in_quote_units"]) * Decimal(self.size_multiple) / Decimal(buy_price)
                        available_to_buy_in_quote = self.connectors[self.exchange].get_available_balance(quote_cur)
                        available_to_buy_in_base = Decimal(available_to_buy_in_quote) / Decimal(buy_price)
                        if order_amount < (available_to_buy_in_base * Decimal(0.99)):
                            self.logger().info(f"Proposal,buy,trading_pair,{trading_pair},buy_price,{buy_price:.8f},buy_price_max,{buy_price_max:.8f},orderbook_bid,{orderbook_bid},amount,{order_amount:.8f}")
                            buy_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                                       order_side=TradeType.BUY, amount=Decimal(order_amount), price=buy_price)
                            proposal.append(buy_order)
                            num_orders += 1
                            self.last_order_time[trading_pair] = time.time()
                        else:
                            skip_proposal_for_balance += 1
                
                sell_order = 0
                """
                Skip if we have an existing sell limit order for this trading pair.
                Skip if reference price is too far from top of book.  Not placing the uncompetitive order helps with exchange API limits.
                """
                if len(self.orders[trading_pair][0]) == 0:
                    if orderbook_ask < sell_price_min * Decimal(1 - self.sell_place_max_distance):
                        skip_proposal_for_distance += 1
                        pass
                    else:

                        """
                        If the order will be the new top of book, place it half way between reference price and old top of book +/- config spread
                        If it won’t be the new top of book, place it at the reference price +/- config spread
                        """
                        if orderbook_ask > sell_price_min:
                            sell_price = Decimal(sell_price_min + orderbook_ask) / 2
                        else:
                            sell_price = sell_price_min

                        """
                        Convert the size units and check available balance.
                        Place order if size looks OK.  
                        """
                        available_to_sell_in_base = self.connectors[self.exchange].get_available_balance(base_cur)
                        order_amount = available_to_sell_in_base

                        if order_amount >= Decimal(self.quote_curs[quote_cur]["size_in_quote_units"]):
                            self.logger().info(f"Proposal,sell,trading_pair,{trading_pair},sell_price,{sell_price:.8f},sell_price_min,{sell_price_min:.8f},orderbook_ask,{orderbook_ask},amount,{order_amount:.8f}")
                            sell_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                                        order_side=TradeType.SELL, amount=Decimal(order_amount), price=sell_price)
                            proposal.append(sell_order)
                            num_orders += 1
                            self.last_order_time[trading_pair] = time.time()
                        else:
                            skip_proposal_for_balance += 1

                """
                Place order.
                """
                proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal)
                self.place_orders(proposal_adjusted)

        """
        Do some summary logging.
        """
        num_actions = num_cancels_unprofitable + num_cancels_outbid + num_orders
        self.logger().info(f"Skipped due to: {skip_proposal_for_cancels} cancels, {skip_proposal_for_count} count, {skip_proposal_for_time} time, {skip_proposal_for_distance} distance and {skip_proposal_for_balance} balance.")
        self.logger().info(f"{time.time()} There were {num_cancels_unprofitable} cancels_unprofitable, {num_cancels_outbid} cancels_outbid and {num_orders} proposals.")

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        proposal_adjusted = self.connectors[self.exchange].budget_checker.adjust_candidates(proposal, all_or_none=True)
        return proposal_adjusted

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        for order in proposal:
            self.place_order(connector_name=self.exchange, order=order)

    def place_order(self, connector_name: str, order: OrderCandidate):
        if order.order_side == TradeType.SELL:
            self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                      order_type=order.order_type, price=order.price)
        elif order.order_side == TradeType.BUY:
            self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                     order_type=order.order_type, price=order.price)

    def cancel_all_orders(self):
        for order in self.get_active_orders(connector_name=self.exchange):
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = (f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.exchange} at {round(event.price, 2)}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)
