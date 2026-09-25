"""在合成 SQLite 中验证异常所得追回，不接触真实账户。"""
import copy
import hashlib
import importlib
import json
import sqlite3

import pytest
from scripts.reconcile_table_profits import reconcile

TableWallet = importlib.import_module('src.chat.features.games.blackjack-web.table_wallet').TableWallet


@pytest.fixture
def case(tmp_path):
    wallet = TableWallet(str(tmp_path / 'coins.db'))
    with wallet._transaction() as c:
        c.executescript('''
            CREATE TABLE user_coins (user_id INTEGER PRIMARY KEY,balance INTEGER NOT NULL);
            CREATE TABLE coin_transactions (transaction_id INTEGER PRIMARY KEY,user_id INTEGER,amount INTEGER,reason TEXT,timestamp TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE coin_red_envelopes (id TEXT PRIMARY KEY,sender_id INTEGER,total_amount INTEGER,remaining_amount INTEGER,refunded_amount INTEGER,status TEXT,count INTEGER,remaining_count INTEGER);
            CREATE TABLE coin_red_envelope_claims (envelope_id TEXT,user_id INTEGER,amount INTEGER);
            INSERT INTO user_coins VALUES (1,20),(2,1000);
            INSERT INTO coin_red_envelopes VALUES ('packet',1,100,100,0,'active',1,1);
        ''')
        rounds=[]
        for key, profit, day in [('r1',30,'2026-09-24'),('r2',40,'2026-09-25')]:
            details=json.dumps({'room_id':key,'base_stake':500})
            at=day+'T12:00:00+08:00'
            c.execute('INSERT INTO table_game_results VALUES (?,?,?,?,?,?,?,?)',(key,1,'landlord','甲','',profit,at,day))
            c.execute('INSERT INTO table_game_escrow VALUES (?,?,?,?,?)',(key,1,100,100+profit,'settled'))
            c.execute('INSERT INTO table_round_details VALUES (?,?,?,?,?)',(key,1,100,100+profit,details))
            rounds.append({'round_key':key,'profit':profit,'game_type':'landlord','settled_at':at,'settled_day':day,'escrow_stake':100,'escrow_payout':100+profit,'details_sha256':hashlib.sha256(details.encode()).hexdigest()})
        c.execute("INSERT INTO table_game_escrow VALUES ('legacy',1,10,15,'settled')")
        c.execute("INSERT INTO table_game_results VALUES ('other',2,'landlord','乙','',50,'2026-09-25T12:01:00+08:00','2026-09-25')")
    manifest={'authorization':'explicit_user_request','operation_id':'test-recovery','reason':'撤销异常盈利','user_id':'1','total':70,'rounds':rounds,'envelopes':[{'id':'packet','total_amount':100}]}
    return wallet,manifest


def dump(wallet):
    with sqlite3.connect(wallet.db_path) as c:
        return '\n'.join(c.iterdump())


def test_dry_run_is_fully_rolled_back(case):
    wallet,m=case
    before=dump(wallet)
    result=reconcile(wallet,m)
    assert result['balance_after']==50 and not result['applied']
    assert dump(wallet)==before


def test_apply_preserves_original_records_and_corrects_every_view(case):
    wallet,m=case
    with sqlite3.connect(wallet.db_path) as c:
        original={t:c.execute('SELECT * FROM '+t).fetchall() for t in ['table_game_results','table_game_escrow','table_round_details']}
    receipt=reconcile(wallet,m,apply=True)
    assert receipt['balance_before']==20 and receipt['envelope_refund']==100
    assert receipt['recovered']==70 and receipt['balance_after']==50
    assert receipt['by_day']=={'2026-09-24':-30,'2026-09-25':-40}
    assert receipt['by_game']=={'landlord':-70}
    wallet._today=lambda:'2026-09-25'
    stats=wallet._statistics('1',None)['stats']
    assert (stats['rounds'],stats['wins'],stats['draws'],stats['net_profit'],stats['max_win'],stats['today_profit'])==(3,1,2,5,5,0)
    assert wallet._statistics('1','landlord')['stats']['net_profit']==0
    assert wallet._leaderboard('1','all',None,100)['self']['net_profit']==5
    assert wallet._leaderboard('1','today','landlord',100)['self']['net_profit']==0
    wallet._today=lambda:'2026-09-24'
    assert wallet._leaderboard('1','today',None,100)['self']['net_profit']==0
    entries=wallet._history('1','landlord',20,0)['entries']
    assert len(entries)==2 and all(e['profit']==0 for e in entries)
    detail=wallet._round_detail('1','r1')
    assert (detail['original_profit'],detail['adjustment'],detail['profit'],detail['payout'])==(30,-30,0,130)
    assert detail['adjustment_reason']=='撤销异常盈利' and detail['details']['room_id']=='r1'
    with sqlite3.connect(wallet.db_path) as c:
        assert dict(c.execute('SELECT user_id,balance FROM user_coins'))=={1:50,2:1000}
        assert c.execute('SELECT SUM(amount),COUNT(*) FROM coin_transactions').fetchone()==(30,3)
        assert c.execute('SELECT status,remaining_amount,refunded_amount FROM coin_red_envelopes').fetchone()==('cancelled',0,100)
        for table,rows in original.items():
            assert c.execute('SELECT * FROM '+table).fetchall()==rows


def test_repeat_and_settlement_retry_cannot_recredit(case):
    wallet,m=case
    reconcile(wallet,m,apply=True)
    before=dump(wallet)
    assert reconcile(wallet,m,apply=True)['replayed']
    assert dump(wallet)==before
    wallet._settle('r1',{'1':130},'landlord')
    assert wallet._round_detail('1','r1')['profit']==0
    with sqlite3.connect(wallet.db_path) as c:
        assert c.execute('SELECT balance FROM user_coins WHERE user_id=1').fetchone()[0]==50
    altered=copy.deepcopy(m);altered['reason']='其他原因'
    with pytest.raises(ValueError,match='不同清单'):
        reconcile(wallet,altered,apply=True)
    altered['operation_id']='another'
    with pytest.raises(ValueError,match='已经冲正'):
        reconcile(wallet,altered,apply=True)


@pytest.mark.parametrize('mutation', ['hash','profit','owner','claimed','insufficient','reserved','missing_round','duplicate','total'])
def test_stale_or_invalid_evidence_is_atomic(case,mutation):
    wallet,m=case
    with sqlite3.connect(wallet.db_path) as c:
        if mutation=='hash': m['rounds'][0]['details_sha256']='0'*64
        elif mutation=='profit': m['rounds'][0]['escrow_payout']+=1
        elif mutation=='owner': c.execute('UPDATE coin_red_envelopes SET sender_id=2')
        elif mutation=='claimed': c.execute("INSERT INTO coin_red_envelope_claims VALUES ('packet',2,100)")
        elif mutation=='insufficient': c.execute('UPDATE user_coins SET balance=-40 WHERE user_id=1')
        elif mutation=='reserved': c.execute("INSERT INTO table_game_escrow VALUES ('live',1,10,NULL,'reserved')")
        elif mutation=='missing_round': c.execute("DELETE FROM table_round_details WHERE round_key='r1'")
        elif mutation=='duplicate': m['rounds'].append(m['rounds'][0])
        elif mutation=='total': m['total']=1
    before=dump(wallet)
    with pytest.raises(ValueError):reconcile(wallet,m,apply=True)
    assert dump(wallet)==before


def test_transaction_failure_rolls_back_refund_debit_and_stats(case):
    wallet,m=case
    with sqlite3.connect(wallet.db_path) as c:
        c.execute("CREATE TRIGGER fail_second BEFORE INSERT ON table_game_adjustments WHEN NEW.round_key='r2' BEGIN SELECT RAISE(ABORT,'故障'); END")
    before=dump(wallet)
    with pytest.raises(sqlite3.IntegrityError):reconcile(wallet,m,apply=True)
    assert dump(wallet)==before


def test_already_expired_refund_is_not_credited_twice(case):
    wallet,m=case
    with sqlite3.connect(wallet.db_path) as c:
        c.execute("UPDATE coin_red_envelopes SET status='expired',remaining_amount=0,refunded_amount=100")
        c.execute('UPDATE user_coins SET balance=balance+100 WHERE user_id=1')
    result=reconcile(wallet,m,apply=True)
    assert result['envelope_refund']==0 and result['balance_after']==50

def test_concurrent_retries_only_debit_once(case):
    from concurrent.futures import ThreadPoolExecutor
    wallet,m=case
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts=list(pool.map(lambda _:reconcile(wallet,m,apply=True),range(2)))
    assert sorted(r['replayed'] for r in receipts)==[False,True]
    with sqlite3.connect(wallet.db_path) as c:
        assert c.execute('SELECT balance FROM user_coins WHERE user_id=1').fetchone()[0]==50
        assert c.execute('SELECT COUNT(*) FROM coin_transactions').fetchone()[0]==3
