"""选品判断的证据边界：缺失不是零、校正需要本期证据、两边品牌等权。"""
from copy import deepcopy
from datetime import timedelta

import pytest

from radar import analyze
from radar.detect import directions
from radar.series import Daily, baseline_average
from tests.test_detection import END, _monthly_state, _rec, make
from tests.test_design import _dir_item, _head_row


def test_unknown_baseline_does_not_become_a_new_surge_or_full_increment(cfg):
    rec = _rec([0] * 113 + [40] * 7, 4.5, available=(END - timedelta(days=60)).isoformat(), ratings=0)
    rec['series']['sales'][:113] = [None] * 113
    rec['series']['bsr'][:113] = [None] * 113
    item = analyze.evaluate('NEW', rec, None, {}, cfg, END)
    assert item['label'] == 'watch' and item['surge'] is None
    assert item['baseline_unknown'] and analyze.increment(item) == 0
    assert '无法估算增量' in analyze.increment_note(item)
    assert '新品起量（对比数据不足，暂不确认爆火）' in item['tags']


def test_known_zero_sales_still_allow_launch_and_positive_short_baseline_still_counts(cfg):
    rec = _rec([0] * 113 + [40] * 7, 4.5, available=(END - timedelta(days=60)).isoformat(), ratings=0)
    item = analyze.evaluate('NEW', rec, None, {}, cfg, END)
    assert item['label'] == 'surge' and not item['baseline_unknown']
    assert analyze.increment(item) > 0
    d = make([5] * 120)
    d.sales[64:90] = [None] * 26
    assert baseline_average(d, 91, 28, cfg['thresholds']['out_of_stock_bsr']) == 5


def test_filtered_positive_sales_are_not_a_zero_baseline(cfg):
    d = make([5] * 113 + [20] * 7)
    d.bsr[:113] = [None] * 113
    assert baseline_average(d, 112, 28, cfg['thresholds']['out_of_stock_bsr']) is None
    from radar.detect.rules import evaluate_surge
    assert evaluate_surge(d, 119, cfg['thresholds']['surge'], cfg['thresholds']['out_of_stock_bsr']) is None


def test_few_complete_months_are_disclosed_without_blocking_potential(cfg):
    rec = _rec([5] * 92 + [8] * 28, 4.5, available=(END - timedelta(days=90)).isoformat(), ratings=20)
    item = analyze.evaluate('NEW', rec, None, {}, cfg, END)
    assert item['label'] == 'potential'
    assert '月度增长尚未验证（完整月份不足）' in item['tags']


def test_revision_cannot_compare_different_cohorts_or_divide_by_zero():
    a = make([10] * 93 + [0] * 27)  # 只有历史截面有效，且历史比值为零
    b = make([0] * 93 + [10] * 18 + [20] * 9)  # 只有本期截面有效
    assert analyze.recent_inflation([a] * 30 + [b] * 30, [], 9) is None
    zero = make([10] * 97 + [0] * 9 + [10] * 14)
    assert analyze.recent_inflation([zero] * 30, [], 9) is None


def test_revision_uses_common_samples_and_common_calendar_dates():
    stable = make([10] * 120)
    new_only = make([0] * 93 + [10] * 18 + [20] * 9)
    assert analyze.recent_inflation([stable] * 30 + [new_only] * 50, [], 9) == 1
    inflated = make([10] * 111 + [15] * 9)
    # 落后 4 天的序列不能把自己的最后一天当作本期最新日期。
    lagging = Daily(inflated.start, inflated.bsr[:-4], inflated.sales[:-4], inflated.price[:-4])
    assert analyze.recent_inflation([lagging] * 30 + [inflated], [], 9) == 1.5


@pytest.mark.parametrize('mode', ['correct', 'tag'])
def test_unknown_inflation_only_warns_and_keeps_raw_money_and_storage(cfg, mode):
    cfg['thresholds']['revision_mode'] = mode
    state = {'asins': {'THIN': _rec([5] * 110 + [12] * 10, 4.5)}}
    before = deepcopy(state)
    rev = {'window': 9, 'median': 0.8, 'items': 100}
    result = analyze.analyze(state, None, ['THIN'], cfg, END, revision=rev)
    item = result['items'][0]
    assert result['revision_check']['state'] == 'uncertain'
    assert not result['revision_check']['applied'] and not analyze.corrects(rev, cfg['thresholds'], None)
    assert item['label'] == 'surge' and item['revision_lost'] == ['突然爆火']
    assert item['revision_uncertain']
    assert 'revised_avg28' not in item
    assert state['asins'] == before['asins']
    raw = analyze.evaluate('THIN', state['asins']['THIN'], None, {}, cfg, END)
    assert analyze.increment(item) == analyze.increment(raw)
    from radar.notify import dingtalk
    sec = analyze.sections([item], 0)
    text = dingtalk.build_text({'title': '雷达', 'report_id': END.isoformat(), 'as_of': END.isoformat(),
                                'sections': sec, 'diff': {'first': True}})
    assert '本期是否偏高未确认，若校正则不算突然爆火' in text
    assert '最近几天的数待修订，校正后不算' not in text


def test_monthly_hot_correction_requires_explicit_shared_decision(cfg):
    row = {'asin': 'M', 'units': 1200, 'rating': 4.5, 'ratings': 500, 'price': 100}
    state = _monthly_state({'M': [1200] * 6}, {'M': row})
    disc = {'period': '202609', 'nodes': [{'path': 'N'}], 'products': {
        'M': {**row, 'node': 'N', 'sources': ['top']}}}
    stats = {'N': {'threshold': 900}}
    rev = {'window': 30, 'median': 0.5, 'items': 100}
    items = analyze.monthly_hot_items(state, disc, stats, cfg, END, [], rev=rev, uncertain=True)[0]
    assert items[0]['label'] == 'hot' and items[0]['recent_avg28'] == 40
    assert items[0]['revision_lost'] == ['持续热销']
    assert analyze.monthly_hot_items(state, disc, stats, cfg, END, [], rev=rev, correction_applied=True)[0] == []


@pytest.mark.parametrize('confirmed', [False, True])
def test_daily_monthly_and_market_share_the_same_correction_decision(cfg, monkeypatch, confirmed):
    row = {'asin': 'M', 'units': 870, 'rating': 4.5, 'ratings': 500, 'price': 100}
    state = _monthly_state({'M': [1200] * 6}, {'M': row})
    state['asins'] = {f'F{k}': _rec([20] * 111 + [40] * 9, 4.5) for k in range(30 if confirmed else 1)}
    disc = {'period': '202609', 'nodes': [{'path': 'N'}], 'products': {
        'M': {**row, 'node': 'N', 'sources': ['top']}}}
    rev = {'window': 9, 'median': 0.5, 'items': 100}
    market_revisions = []

    def market(_state, _today, _cfg, _deal, revision):
        market_revisions.append(revision)
        return {}

    monkeypatch.setattr(analyze.universe, 'node_tide', market)
    result = analyze.analyze(state, disc, list(state['asins']), cfg, END, revision=rev)
    assert result['revision_check']['applied'] is confirmed
    assert market_revisions == [rev if confirmed else None]
    monthly = [item for item in result['items'] if item['asin'] == 'M']
    if confirmed:
        assert not monthly  # 29 件/天校正后低于半年日均的 70%，与日数据一起校正
    else:
        assert monthly[0]['recent_avg28'] == 29 and monthly[0]['revision_lost'] == ['持续热销']
        assert monthly[0]['revision_uncertain']


@pytest.mark.parametrize('factor', [0, -1, 1, 1.2, float('inf'), float('nan')])
def test_invalid_or_upward_revision_does_not_trigger_downward_correction(cfg, factor):
    assert analyze.usable_revision({'window': 9, 'median': factor, 'items': 100}, cfg['thresholds']) is None


def test_feature_shares_are_brand_weighted_on_both_sides(cfg):
    # A 的 10 款里仅 1 款有凹槽，B 有：不能因为两个品牌“任一款有”就声称占比 100%。
    growth = [_dir_item(f'A{k}', '书架', 'Fluted Bookshelf' if k == 0 else 'Bookshelf', 'A') for k in range(10)]
    growth += [_dir_item('B', '书架', 'Fluted Bookshelf', 'B')]
    heads = [_head_row(f'H{k}', '书架', 'Fluted Bookshelf' if k < 4 else 'Bookshelf', 1000-k) for k in range(10)]
    out = directions.compute(growth, {'asins': {}}, {}, cfg, disc={'products': {h['asin']: h for h in heads}})[0]
    f = out['features']['工艺']['base'][0]
    assert f['label'] == '凹槽竖纹' and f['count'] == 2
    assert f['share'] == 0.55 and f['head_share'] == 0.4
    assert not out['features']['工艺']['more']
    # 给头部的一个品牌重复同款，等权对比占比不应改变。
    dup = {**heads[0], 'asin': 'DUP', 'units': 800}
    out2 = directions.compute(growth, {'asins': {}}, {}, cfg,
                              disc={'products': {h['asin']: h for h in heads + [dup]}})[0]
    assert out2['features']['工艺'] == out['features']['工艺']
    from radar import narrative
    fact = narrative.direction_facts({'directions': [out]})[0]
    assert '品牌内商品特征占比' in fact['依据']
    evidence = fact['基本款']['工艺'][0]
    assert '品牌均占比 55%' in evidence and '头部品牌均占比 40%' in evidence


def test_unknown_brands_cannot_supply_independent_support(cfg):
    unknown = [_dir_item('U1', '书架', 'Fluted Bookshelf', ''), _dir_item('U2', '书架', 'Fluted Bookshelf', '')]
    assert not directions.ranked(unknown, cfg)
    assert not directions.compute(unknown, {'asins': {}}, {}, cfg)
    growth = [_dir_item('A', '书架', 'Bookshelf', 'A'), _dir_item('B', '书架', 'Bookshelf', 'B'), *unknown]
    out = directions.compute(growth, {'asins': {}}, {}, cfg)[0]
    assert out['n_brands'] == 2 and not out['features']['工艺']['more'] and not out['features']['工艺']['base']


def test_many_heads_from_one_brand_are_not_enough_for_comparison(cfg):
    growth = [_dir_item('A', '书架', 'Fluted Bookshelf', 'A'), _dir_item('B', '书架', 'Fluted Bookshelf', 'B')]
    heads = [{**_head_row(f'H{k}', '书架', 'Bookshelf', 1000-k), 'brand': 'ONE'} for k in range(15)]
    out = directions.compute(growth, {'asins': {}}, {}, cfg, disc={'products': {h['asin']: h for h in heads}})[0]
    assert out['compare']['mode'] is None and out['compare']['n_head_brands_all'] == 1
    assert not out['features']['工艺']['more']
