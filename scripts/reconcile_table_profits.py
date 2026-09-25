"""管理员离线冲正：按已审核清单追回本人异常盈利，默认只演练并回滚。"""
import argparse
from collections import defaultdict
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys


def reconcile(wallet, manifest: dict, *, apply: bool = False) -> dict:
    """红包退款、逐局扣款、统计调整和幂等回执共用一个写事务。"""
    if manifest.get('authorization') != 'explicit_user_request':
        raise ValueError('清单缺少明确授权标记')
    uid = int(manifest['user_id'])
    operation = manifest['operation_id']
    reason = manifest['reason']
    rounds = manifest['rounds']
    envelopes = manifest.get('envelopes', [])
    if uid <= 0 or not isinstance(operation, str) or not operation or not reason or not rounds:
        raise ValueError('冲正清单字段无效')
    if len({r['round_key'] for r in rounds}) != len(rounds) or len({e['id'] for e in envelopes}) != len(envelopes):
        raise ValueError('清单有重复牌局或红包')
    if any(type(r['profit']) is not int or r['profit'] <= 0 for r in rounds):
        raise ValueError('只允许逐局撤销已核实的正盈利')
    total = sum(r['profit'] for r in rounds)
    if type(manifest['total']) is not int or total != manifest['total']:
        raise ValueError('清单总额不一致')
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    with wallet._transaction() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS table_game_reconciliations (
            operation_id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL,
            user_id INTEGER NOT NULL, receipt TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        previous = c.execute('SELECT manifest_sha256, user_id, receipt FROM table_game_reconciliations WHERE operation_id=?', (operation,)).fetchone()
        if previous:
            if previous[0] != digest or previous[1] != uid:
                raise ValueError('同一操作已使用不同清单，拒绝重复扣除')
            receipt = json.loads(previous[2])
            count, delta = c.execute('SELECT COUNT(*), SUM(profit_delta) FROM table_game_adjustments WHERE operation_id=? AND user_id=?', (operation, uid)).fetchone()
            if count != len(rounds) or delta != -total:
                raise ValueError('既有冲正与回执不一致，须人工核查')
            return {**receipt, 'replayed': True, 'applied': True}
        balance = c.execute('SELECT balance FROM user_coins WHERE user_id=?', (uid,)).fetchone()
        if balance is None:
            raise ValueError('找不到指定钱包')
        before = balance[0]
        if c.execute("SELECT 1 FROM table_game_escrow WHERE user_id=? AND status='reserved' LIMIT 1", (uid,)).fetchone():
            raise ValueError('本人有未完成牌局托管，暂不冲正')
        # 所有核对和后续修改都持有同一个 SQLite 写锁，领取和到期退款不能穿插。
        for r in rounds:
            row = c.execute('''SELECT r.profit,r.game_type,r.settled_at,r.settled_day,
                e.stake,e.payout,e.status,d.stake,d.payout,d.details
                FROM table_game_results r JOIN table_game_escrow e USING(round_key,user_id)
                JOIN table_round_details d USING(round_key,user_id)
                WHERE r.round_key=? AND r.user_id=?''', (r['round_key'], uid)).fetchone()
            expected = (r['profit'], r['game_type'], r['settled_at'], r['settled_day'],
                        r['escrow_stake'], r['escrow_payout'], 'settled', r['escrow_stake'], r['escrow_payout'])
            if row is None or row[:9] != expected or row[5] - row[4] != r['profit']:
                raise ValueError('原始结算与清单不一致：' + r['round_key'])
            if hashlib.sha256(row[9].encode()).hexdigest() != r['details_sha256']:
                raise ValueError('牌局详情哈希改变：' + r['round_key'])
            if c.execute('SELECT 1 FROM table_game_adjustments WHERE round_key=? AND user_id=?', (r['round_key'], uid)).fetchone():
                raise ValueError('牌局已经冲正，拒绝二次扣除')
        refunds = []
        for e in envelopes:
            row = c.execute('''SELECT sender_id,total_amount,remaining_amount,refunded_amount,
                status,count,remaining_count FROM coin_red_envelopes WHERE id=?''', (e['id'],)).fetchone()
            claims = c.execute('SELECT COUNT(*) FROM coin_red_envelope_claims WHERE envelope_id=?', (e['id'],)).fetchone()[0]
            if row is None or row[0] != uid or row[1] != e['total_amount'] or claims:
                raise ValueError('红包归属、金额或领取记录改变：' + e['id'])
            if row[4] == 'active' and row[2] == row[1] and row[3] == 0 and row[5] == row[6]:
                refunds.append({'id': e['id'], 'amount': row[2]})
            elif not (row[4] in ('expired', 'cancelled') and row[2] == 0 and row[3] == row[1]):
                raise ValueError('红包状态不符合完整未领取或已全额退款：' + e['id'])
        refunded = sum(e['amount'] for e in refunds)
        if before + refunded < total:
            raise ValueError('本人余额与未领取红包不足以全额扣回')
        receipts = []
        for e in refunds:
            c.execute("UPDATE coin_red_envelopes SET status='cancelled',remaining_amount=0,refunded_amount=? WHERE id=? AND status='active'", (e['amount'], e['id']))
            wallet._credit(c, uid, e['amount'], f"异常盈利核查：撤销本人未领取红包 {e['id']} 退款 [{operation}]")
            receipts.append({'kind': 'refund', 'envelope_id': e['id'], 'amount': e['amount'], 'transaction_id': c.execute('SELECT last_insert_rowid()').fetchone()[0]})
        by_day, by_game = defaultdict(int), defaultdict(int)
        for r in rounds:
            wallet._credit(c, uid, -r['profit'], f"{reason}：牌局 {r['round_key']} [{operation}]")
            transaction_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('''INSERT INTO table_game_adjustments
                (round_key,user_id,operation_id,original_profit,profit_delta,reason,evidence_sha256,coin_transaction_id)
                VALUES (?,?,?,?,?,?,?,?)''', (r['round_key'], uid, operation, r['profit'], -r['profit'], reason, r['details_sha256'], transaction_id))
            receipts.append({'kind': 'recovery', 'round_key': r['round_key'], 'amount': -r['profit'], 'transaction_id': transaction_id})
            by_day[r['settled_day']] -= r['profit']
            by_game[r['game_type']] -= r['profit']
        after = c.execute('SELECT balance FROM user_coins WHERE user_id=?', (uid,)).fetchone()[0]
        if after != before + refunded - total or after < 0:
            raise ValueError('钱包守恒校验失败')
        adjusted = c.execute('''SELECT COUNT(*), SUM(r.profit+a.profit_delta)
            FROM table_game_results r JOIN table_game_adjustments a USING(round_key,user_id)
            WHERE a.operation_id=? AND a.user_id=?''', (operation, uid)).fetchone()
        if adjusted != (len(rounds), 0):
            raise ValueError('逐局冲正校验失败')
        receipt = {'operation_id': operation, 'manifest_sha256': digest, 'user_id': str(uid),
                   'balance_before': before, 'envelope_refund': refunded, 'recovered': total,
                   'balance_after': after, 'rounds': len(rounds), 'by_day': dict(by_day),
                   'by_game': dict(by_game), 'transactions': receipts, 'replayed': False}
        c.execute('INSERT INTO table_game_reconciliations (operation_id,manifest_sha256,user_id,receipt) VALUES (?,?,?,?)',
                  (operation, digest, uid, json.dumps(receipt, ensure_ascii=False)))
        if not apply:
            c.rollback()
        return {**receipt, 'applied': apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--apply', action='store_true', help='实际提交；省略则完整演练后回滚')
    args = parser.parse_args()
    if not Path(args.db).is_file():
        raise ValueError('数据库路径不存在，拒绝创建空库')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    wallet = importlib.import_module('src.chat.features.games.blackjack-web.table_wallet').TableWallet(args.db)
    receipt = reconcile(wallet, json.loads(Path(args.manifest).read_text(encoding='utf-8')), apply=args.apply)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
