#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Author : <github.com/tintinweb>
import random
import json
import logging
from types import SimpleNamespace
from evmlab.tools.statetests import rndval, randomtest
from evmlab.tools.statetests.rndval.base import WeightedRandomizer

from evmlab.tools.statetests.rndval import RndCodeBytes


logger = logging.getLogger("evmlab.tools.statetest")


class Account(object):

    def __init__(self, address, balance=None, code=None, nonce=None, storage=None):
        self.address = address
        self.balance = balance if balance is not None else rndval.RndHexInt(_min=2**24-1)
        self.code = code if code is not None else ''
        self.nonce = nonce
        self.storage = storage if storage is not None else {}

    @property
    def __dict__(self):
        return {"balance": self.balance,
                "code": self.code,
                "nonce": self.nonce,
                "storage": self.storage}



class StateTestTemplate(object):

    def __init__(self, nonce=None, codegenerators={}, datalength=None,
                 fill_prestate_for_tx_to=True, fill_prestate_for_args=False, _config=None):
        ### global settings
        self._nonce = nonce if nonce is not None else str(rndval.RndV())
        self._config = _config
        ### set by setters below
        self._codegenerators = None  # default
        self._codegenerators_weighted = None
        self._datalength = None

        self._fill_prestate_for_tx_to = fill_prestate_for_tx_to
        self._fill_prestate_for_args = fill_prestate_for_args

        ## set the using a defined interface
        self.codegens = codegenerators  # sets _codegenerators and _codegenerators_weighted
        self.datalength = datalength  # sets _datalength

        # other
        self._fill_counter = 0  # track how often we've filled from this template

        ### info
        self._info = SimpleNamespace(fuzzer="evmlab",
                                     comment=self._config_get("info.comment", "evmlab"),
                                     filledwith="evmlab randomfuzz")

        ### env
        self._env = SimpleNamespace(currentCoinbase=self._config_get("env.coinbase", rndval.RndSourceAddress()),
                                    currentDifficulty=self._config_get("env.difficulty", "0x20000"),
                                    currentGasLimit=self._config_get("env.gaslimit", "0x1312D00"),
                                    currentNumber=self._config_get("env.number", "1"),
                                    currentTimestamp=self._config_get("env.timestamp", "1000"),
                                    previousHash=self._config_get("env.previousHash", rndval.RndHash32()))

        ### post
        self._post = {"Byzantium": [
                            {  # dummy to make statetests happy
                                "hash": "0x00000000000000000000000000000000000000000000000000000000deadc0de",
                                "logs": "0x00000000000000000000000000000000000000000000000000000000deadbeef",
                                "indexes": {"data": 0, "gas": 0, "value": 0}
                            }]
                     }

        ### pre
        self._pre = {}

        ### transaction
        self._transaction = SimpleNamespace(secretKey="0x45a915e4d060149eb4365960e6a7a45f334393093061116b197e3240065ff2d8",
                                            data=[RndCodeBytes().generate(length=self._datalength)],
                                            gasLimit=[rndval.RndTransactionGasLimit(_min=self._config_getint("transaction.gaslimit.random.min",34*14000))],
                                            gasPrice=rndval.RndGasPrice(),
                                            nonce=self._nonce,
                                            to=rndval.RndDestAddressOrZero(),
                                            value=[rndval.RndHexInt(_min=self._config_getint("transaction.value.random.min", 0),
                                                                    _max=self._config_getint("transaction.value.random.max", 2**24))])

    def _config_getint(self, key, default=None):
        if not self._config or not self._config.statetest:
            return default
        return self._config.statetest.getint(key, default)

    def _config_get(self, key, default=None):
        if not self._config or not self._config.statetest:
            return default
        return self._config.statetest.get(key, default)

    def _config_getbool(self, key, default=None):
        if not self._config or not self._config.statetest:
            return default
        return self._config.statetest.getboolean(key, default)

    def _random_storage(self, _min=0, _max=10):
        hx = rndval.RndHex32()
        rnd_vals = (hx.generate() for _ in range(random.randint(_min, _max)))
        return {hx:hx for hx in rnd_vals}


    def _autofill_prestates_from_transaction(self, tx):
        logger.debug("autofill from tx.to")

        if tx.to in self.pre:
            # prestate already exists, renew it?
            if self._fill_counter % self._config_getint("prestate.txto.renew.every.x.rounds", default=1) != 0:
                # do not renew
                logger.debug("autofill from tx.to - not renewing prestate due to prestate.txto.renew.every.x.rounds")
                return

        # force renewal of prestate
        self._autofill_prestate(tx.to, force=True)

        return self

    def _autofill_prestate(self, address, force=False):
        logger.debug("autofill prestate")
        if address in self.pre and not force:
            # already there
            logger.debug("autofill prestate - skipping address already exists (and not using force)")
            return self

        if address.replace("0x","") not in rndval.RndAddress.addresses[rndval.RndAddressType.SENDING_ACCOUNT]+rndval.RndAddress.addresses[rndval.RndAddressType.STATE_ACCOUNT]:
            # skip non state accounts
            return self
        # not a precompiled address?
        # add a random prestate for the address we interact with in the tx
        ### random code
        ### same nonce
        ### random balance
        ### random storage

        codelength_min, codelength_max = self._config_getint("prestate.random.code.length.min", None), self._config_getint("prestate.random.code.length.max", None)
        if codelength_min is not None:
            if codelength_min == codelength_max or codelength_max is None:
                codelength = codelength_min
            else:
                codelength = random.randint(codelength_min, codelength_max)
        else:
            codelength = None

        self.add_prestate(address="0x%s"%address.replace("0x",""),
                          code=self.pick_codegen().generate(length=codelength),  # limit length, main code is in first prestate
                          storage=self._random_storage(_min=self._config_getint("prestate.storage.random.slots.min",0),
                                                       _max=self._config_getint("prestate.storage.random.slots.max",2)))

        return self

    def _autofill_prestates_from_stack_arguments(self, tx):
        # todo: hacky hack
        logger.debug("autofill from stack")
        # config
        config_renew_every_x_rounds = self._config_getint("prestate.other.renew.every.x.rounds", default=1) or 1  # 1== every round
        config_renew_limit = self._config_getint("prestate.other.renew.limit.per.round", default=0)

        nr_of_prestates_renewed_this_round = nr_of_prestates_added_this_round = 0
        all_addresses = set()

        for cg in self._codegenerators.values():
            try:
                all_addresses.update(cg._addresses_seen)
                cg._addresses_seen = set()  # reset addresses seen
            except (KeyError, AttributeError) as ae:
                #print(ae)
                pass

        # do not handle precompiled accounts.
        # remove tx.to to avoid renewing it. this is handled in autifille
        all_addresses = list(all_addresses.difference(rndval.RndAddress.addresses[rndval.RndAddressType.PRECOMPILED] + [tx.to.replace("0x","")]))

        # shuffle list to avoid bailing always on the same objects (set is ordered)
        random.shuffle(all_addresses)

        for addr in all_addresses:
            #print(addr)
            if "0x%s"%addr.replace("0x","") in self.pre:
                # address exists, renew it in this round?
                if self._fill_counter % config_renew_every_x_rounds != 0:
                    # do not renew, skip
                    logger.debug(
                        "autofill from stack - not renewing prestate due to prestate.other.renew.every.x.rounds")
                    continue
                # address exists, did we already hit the renewal limit? only renew up to <limit> accounts (but add new ones)
                # config_renew_limit == 0 - disabled, otherwise max nr of accounts to renew in this filling round
                if config_renew_limit and nr_of_prestates_renewed_this_round >= config_renew_limit:
                    # do not renew, skip
                    logger.debug(
                        "autofill from stack - not renewing prestate due to prestate.other.renew.limit.per.round")
                    continue
                nr_of_prestates_renewed_this_round += 1
            # force overwriting the prestate; dups are handled in the loop.
            self._autofill_prestate(addr, force=True)
            nr_of_prestates_added_this_round += 1

        logger.info("nr_of_prestates_added_this_round = %d" % nr_of_prestates_added_this_round)
        logger.info("nr_of_prestates_renewed_this_round = %d" % nr_of_prestates_renewed_this_round)



    def _build(self):
        # clone the tx namespace and replace the generator with a concrete value (we can then refer to that value later)
        tx = SimpleNamespace(**self.transaction.__dict__)
        if isinstance(tx.to, rndval.RndAddress):
            tx.to = tx.to.generate()

        env = SimpleNamespace(**self.env.__dict__)
        if isinstance(env.currentCoinbase, rndval.RndAddress):
            env.currentCoinbase = env.currentCoinbase.generate()

        if self._fill_prestate_for_tx_to:
            self._autofill_prestates_from_transaction(tx)

        if self._fill_prestate_for_args:
            self._autofill_prestates_from_stack_arguments(tx)

        self.add_prestate(address=env.currentCoinbase, code="")

        return {"randomStatetest": {
                       "_info": self.info.__dict__,
                       "env": env.__dict__,
                       "post": self.post,
                       "pre": {address: a.__dict__ for address,a in self.pre.items()},
                       "transaction": tx.__dict__}}

    @property
    def codegens(self):
        return self._codegenerators

    @codegens.setter
    def codegens(self, weighted_codegens):
        self._codegenerators = {engine: engine(_config=self._config.codegen if self._config else None) for engine in
                                weighted_codegens.keys()}  # instantiate available code generators
        self._codegenerators_weighted = WeightedRandomizer(
            {self._codegenerators[engine]: weight for engine, weight in weighted_codegens.items()})  #

    @property
    def datalength(self):
        return self._datalength

    @datalength.setter
    def datalength(self, datalength):
        self._datalength = datalength

    @property
    def info(self):
        return self._info

    @property
    def env(self):
        return self._env

    @property
    def post(self):
        return self._post

    @property
    def pre(self):
        return self._pre

    @pre.setter
    def pre(self, accounts):
        self._pre = accounts

    @property
    def transaction(self):
        return self._transaction

    @transaction.setter
    def transaction(self, transaction):
        self._transaction = transaction

    def add_prestate(self, address, balance=None, code=None, nonce=None, storage=None):
        acc = Account(address=address,
                      balance=balance,
                      code=code if code is not None else self.pick_codegen().generate(),
                      nonce=nonce if nonce is not None else self._nonce,  # use global nonce if not explicitly set
                      storage=storage)
        self.pre[acc.address] = acc

    def add_precomipled_prestates(self, force=False):
        # It's better to already have the precompiles there, otherwise it just addres
        # false positives due to the precompiles not existing in the trie.
        # That will lead to consensus errors, but it's not an issue on mainnet,
        # because all precompiles already exist there
        for addr in rndval.RndAddress.addresses[rndval.RndAddressType.PRECOMPILED]:
            addr = "0x%s"%addr.replace("0x","")
            if addr not in self._pre or force:
                self.add_prestate(address=addr, balance="0x01", code="")

    def pick_codegen(self, name=None):
        if name:
            return self._codegenerators[name]
        return self._codegenerators_weighted.random()

    @property
    def __dict__(self):
        return self._build()

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def __iter__(self):
        yield self._build()

    def json(self):
        return json.dumps(self.__dict__, cls=randomtest.RandomTestsJsonEncoder)

    def fill(self):
        self._fill_counter += 1
        # will be filled by _build
        return json.loads(self.json())


if __name__=="__main__":
    st = StateTestTemplate(nonce="0x1d",
                           codegenerators={rndval.RndCodeBytes: 5,
                                           rndval.RndCodeInstr: 25,
                                           rndval.RndCodeSmart2: 70},
                           fill_prestate_for_args=True,
                           fill_prestate_for_tx_to=True)
    st.info.fuzzer = "evmlab tin"

    #st.env.currentCoinbase = rndval.RndDestAddress()
    #st.env.previousHash = rndval.RndHash32()

    #st.add_prestate(address="ffffffffffffffffffffffffffffffffffffffff")
    #st.add_prestate(address="1000000000000000000000000000000000000000")
    #st.add_prestate(address="b94f5374fce5edbc8e2a8697c15331677e6ebf0b")
    #st.add_prestate(address="c94f5374fce5edbc8e2a8697c15331677e6ebf0b")
    #st.add_prestate(address="d94f5374fce5edbc8e2a8697c15331677e6ebf0b")
    #st.add_prestate(address="a94f5374fce5edbc8e2a8697c15331677e6ebf0b")

    import pprint

    pprint.pprint(st.__dict__)
    pprint.pprint(st.__dict__)







