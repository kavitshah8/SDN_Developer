#!/usr/bin/env python
"""
Implementation of an OpenFlow flow table

@author: Colin Scott (cs@cs.berkeley.edu)

"""
from collections import namedtuple
from pox.openflow.libopenflow_01 import *
from pox.openflow.flow_table import *

import time

import unittest
import sys
import os.path
sys.path.append(os.path.dirname(__file__) + "/../..")

class TableEntryTest(unittest.TestCase):
  def test_create(self):
    e = TableEntry(priority=5, cookie=0xDEADBEEF, match=ofp_match(), actions=[ofp_action_output(port=1)])
    self.assertEqual(e.priority, 5)
    self.assertEqual(e.cookie, 0xDEADBEEF)
    self.assertEqual(e.actions, [ ofp_action_output(port=1) ])

  def test_from_flow_mod(self):
    e = TableEntry.from_flow_mod(ofp_flow_mod(priority=5, cookie=0x31415926, actions=[ofp_action_output(port=5)]))
    self.assertEqual(e.priority, 5)
    self.assertEqual(e.cookie, 0x31415926)
    self.assertEqual(e.actions, [ ofp_action_output(port=5) ])

  def test_to_flow_mod(self):
    e = TableEntry(priority=5,cookie=0xDEADBEEF, match=ofp_match(), actions=[ofp_action_output(port=1)])
    f = e.to_flow_mod(command = OFPFC_ADD)
    self.assertEqual(f.priority, 5)
    self.assertEqual(e.cookie, 0xDEADBEEF)
    self.assertEqual(e.actions, [ ofp_action_output(port=1)])

  def test_is_expired(self):
    e = TableEntry(now=0, idle_timeout=5, hard_timeout=10)
    self.assertEqual(e.idle_timeout, 5)
    self.assertEqual(e.hard_timeout, 10)
    self.assertFalse(e.is_expired(now=1))
    self.assertFalse(e.is_expired(now=5))
    self.assertTrue(e.is_expired(now=7))
    e.touch_packet(12, now=5)
    self.assertEqual(e.counters["bytes"], 12)
    self.assertEqual(e.counters["packets"], 1)
    self.assertFalse(e.is_expired(now=1))
    self.assertFalse(e.is_expired(now=7))
    self.assertFalse(e.is_expired(now=10))
    e.touch_packet(12, now=9)
    self.assertTrue(e.is_expired(now=11))

    e2 = TableEntry(now=0, idle_timeout=0, hard_timeout=10)
    self.assertFalse(e2.is_expired(now=1))
    self.assertFalse(e2.is_expired(now=9))
    self.assertTrue(e2.is_expired(now=11))

class FlowTableTest(unittest.TestCase):
  def test_remove_matching_entries(self):
    """ test that simple removal of a flow works"""
    def table():
      t = FlowTable()
      t.add_entry(TableEntry(priority=6, cookie=0x1, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:01"),nw_src="1.2.3.4"), actions=[ofp_action_output(port=5)]))
      t.add_entry(TableEntry(priority=5, cookie=0x2, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:02"), nw_src="1.2.3.0/24"), actions=[ofp_action_output(port=6)]))
      t.add_entry(TableEntry(priority=1, cookie=0x3, match=ofp_match(), actions=[]))
      return t

    for (match, priority, strict, remaining) in (
          (ofp_match(), 0, False, []), #non-strict wildcard removes everything
          (ofp_match(), 0, True, [1,2,3]), # strict wildcard with wrong prio removes nothing
          (ofp_match(), 1, True, [1,2]), # strict wildcard with right prio removes only flow 3
          (ofp_match(nw_src="1.2.3.0/24"), 1, False, [3]), # non-strict subnet removes 1+2
          (ofp_match(nw_src="1.2.3.0/24"), 6, True, [1,2,3]), # does not match dl_src => noop
          (ofp_match(dl_src=EthAddr("00:00:00:00:00:02"), nw_src="1.2.3.0/24"), 5, True, [1,3]), # exactly matches #2
          ):
      t=table()
      t.remove_matching_entries(match, priority=priority, strict=strict)
      self.assertEqual([e.cookie for e in t._table], remaining)

  def test_remove_expired_entries(self):
    """ test that flow can get expired as time passes """
    t = FlowTable()
    for (cookie, idle, hard) in ( (1, 5, 20), (2, 5, 20), (3, 0, 20), (4, 0, 0) ):
      t.add_entry(TableEntry(now=0, cookie=cookie, idle_timeout=idle, hard_timeout=hard))

    for (time, touch, remaining) in (
            (1, [], [1,2,3,4]), # at time 1, everyone's happy
            (3, [2], [1,2,3,4]), # at time 3, flow 2 gets touched
            (6, [], [2,3,4]), # at time 6, flow 1 expires
            (9, [], [3,4]), # at time 9, flow 2 expires
            (21, [], [4]), # at time 21, flow 3 expires
            (99999999, [], [4]), # 4 would still live at the end of days
            ):
      [e.touch_packet(1, now=time) for e in t.entries if e.cookie in touch]
      t.remove_expired_entries(now=time)
      self.assertEqual([e.cookie for e in t.entries ], remaining)

class SwitchFlowTableTest(unittest.TestCase):
  def test_process_flow_mod_add(self):
    """ test that simple insertion of a flow works"""
    t = SwitchFlowTable()
    t.process_flow_mod(ofp_flow_mod(priority=5, cookie=0x31415926, actions=[ofp_action_output(port=5)]))
    self.assertEqual(len(t._table), 1)
    e = t._table[0]
    self.assertEqual(e.priority, 5)
    self.assertEqual(e.cookie, 0x31415926)
    self.assertEqual(e.actions, [ ofp_action_output(port=5)])

  def test_process_flow_mod_modify(self):
    """ test that simple removal of a flow works"""
    def table():
      t = SwitchFlowTable()
      t.add_entry(TableEntry(priority=6, cookie=0x1, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:01"),nw_src="1.2.3.4"), actions=[ofp_action_output(port=5)]))
      t.add_entry(TableEntry(priority=5, cookie=0x2, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:02"), nw_src="1.2.3.0/24"), actions=[ofp_action_output(port=6)]))
      t.add_entry(TableEntry(priority=1, cookie=0x3, match=ofp_match(), actions=[]))
      return t

    t = table()
    t.process_flow_mod(ofp_flow_mod(command = OFPFC_MODIFY, match=ofp_match(), actions = [ofp_action_output(port=1)]))
    self.assertEquals([e.cookie for e in t.entries if e.actions == [ofp_action_output(port=1)] ], [1,2,3])
    self.assertEquals(len(t.entries), 3)

    t = table()
    t.process_flow_mod(ofp_flow_mod(command = OFPFC_MODIFY, match=ofp_match(nw_src="1.2.0.0/16"), actions = [ofp_action_output(port=8)]))
    self.assertEquals([e.cookie for e in t.entries if e.actions == [ofp_action_output(port=8)] ], [1,2])
    self.assertEquals(len(t.entries), 3)

    # non-matching OFPFC_MODIFY acts as add
    t = table()
    t.process_flow_mod(ofp_flow_mod(cookie=5, command = OFPFC_MODIFY, match=ofp_match(nw_src="2.2.0.0/16"), actions = [ofp_action_output(port=8)]))
    self.assertEquals(len(t.entries), 4)
    self.assertEquals([e.cookie for e in t.entries if e.actions == [ofp_action_output(port=8)] ], [5])

  def test_process_flow_mod_modify_strict(self):
    """ test that simple removal of a flow works"""
    def table():
      t = SwitchFlowTable()
      t.add_entry(TableEntry(priority=6, cookie=0x1, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:01"),nw_src="1.2.3.4"), actions=[ofp_action_output(port=5)]))
      t.add_entry(TableEntry(priority=5, cookie=0x2, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:02"), nw_src="1.2.3.0/24"), actions=[ofp_action_output(port=6)]))
      t.add_entry(TableEntry(priority=1, cookie=0x3, match=ofp_match(), actions=[]))
      return t

    t = table()
    t.process_flow_mod(ofp_flow_mod(command = OFPFC_MODIFY_STRICT, priority=1, match=ofp_match(), actions = [ofp_action_output(port=1)]))
    self.assertEquals([e.cookie for e in t.entries if e.actions == [ofp_action_output(port=1)] ], [3])
    self.assertEquals(len(t.entries), 3)

    t = table()
    t.process_flow_mod(ofp_flow_mod(command = OFPFC_MODIFY_STRICT, priority=5, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:02"), nw_src="1.2.3.0/24"), actions = [ofp_action_output(port=8)]))
    self.assertEquals([e.cookie for e in t.entries if e.actions == [ofp_action_output(port=8)] ], [2])
    self.assertEquals(len(t.entries), 3)


class NOMFlowTableTest(unittest.TestCase):
  def test_process_flow_removed(self):
    """ test that simple removal of a flow works"""
    t = NOMFlowTable()
    t.add_entry(TableEntry(priority=5, cookie=0x31415926, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:01")), actions=[ofp_action_output(port=5)]))
    t.add_entry(TableEntry(priority=5, cookie=0x31415927, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:02")), actions=[ofp_action_output(port=6)]))
    self.assertEqual(len(t._table), 2)
    # remove the first flow
    t.process_flow_removed(ofp_flow_removed(priority=5, cookie=0x31415926, match=ofp_match(dl_src=EthAddr("00:00:00:00:00:01"))))
    self.assertEqual(len(t._table), 1)
    self.assertEqual(t._table[0].cookie, 0x31415927)
    # removing a non-matching-flow => NOOP
    for non_matching in [
        { 'cookie': 0x31415926, 'match':ofp_match(dl_src=EthAddr("00:00:00:00:00:01")) }, # already gone
        { 'cookie': 0x31415928, 'match':ofp_match(dl_src=EthAddr("00:00:00:00:00:02")) }, # cookie doesn't fit
        { 'cookie': 0x31415927, 'match':ofp_match(dl_src=EthAddr("00:00:00:00:00:02"), nw_src=IPAddr("1.2.3.4")) }, # extra match field
        ]:
      t.process_flow_removed(ofp_flow_removed(**non_matching))
      self.assertEqual(len(t._table), 1)
      self.assertEqual(t._table[0].cookie, 0x31415927)


if __name__ == '__main__':
  unittest.main()
