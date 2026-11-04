import csv
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.mart import (
    Dimension,
    ExportDefinition,
    MartEngine,
    MartModel,
    Measure,
    WorkspaceStore,
)
from src.mart.capacity import (
    Measurement,
    ResourceCancelled,
    ResourceLimitError,
    ResourcePreflight,
)


def free(value):
    return Measurement(value, 'test', 'test', datetime.now(timezone.utc))


class ResourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WorkspaceStore(self.root / 'mart')
        self.store.create()
        self.model = MartModel('M', 'f', (Dimension('d', ('customer',), ('name',)),), (Measure('amount', 'amount', 18, 2, 'USD', kind='cost'),), 'period', (), ('id',))
        self.source = self.root / 'source.csv'
        self.write('a')
        self.guard = ResourcePreflight(reserve_bytes=0, space_probe=lambda _: free(10 * 1024**3))

    def write(self, value):
        with self.source.open('w', encoding='utf-8', newline='') as out:
            writer = csv.writer(out)
            writer.writerow(['period', 'id', 'customer', 'name', 'amount'])
            writer.writerow(['2026-01', '1', 'c', value, '1.00'])

    def test_native_flow(self):
        engine = MartEngine(self.store, self.guard)
        result = engine.load_snapshot(self.model, [self.source], {'period':'2026-01'}, [self.source.name], complete=True)
        self.assertEqual(result['rows'], 1)
        candidate = engine.create_export(ExportDefinition('output', ('period','id','customer','name','amount')))
        engine.approve_export(candidate['candidate'], 'reviewer')
        engine.publish_local(candidate['candidate'], self.root / 'published.csv')
        engine.backup(self.root / 'backup')
        self.assertTrue((self.root / 'published.csv').exists())

    def test_unknown_unsupported_and_insufficient(self):
        def check(guard):
            return guard('load', self.store, self.source.stat().st_size, files=[self.source], model=self.model)
        self.assertTrue(check(self.guard)['allowed'])
        self.write('a' * 125)
        self.assertTrue(check(self.guard)['allowed'])
        self.write('a' * 126)
        self.assertFalse(check(self.guard)['allowed'])
        self.write('a')
        unknown = ResourcePreflight(reserve_bytes=0, space_probe=lambda _: free(None))
        self.assertFalse(check(unknown)['allowed'])
        low = ResourcePreflight(reserve_bytes=0, space_probe=lambda _: free(1))
        self.assertFalse(check(low)['allowed'])
        unknown_memory = ResourcePreflight(reserve_bytes=0, space_probe=lambda _: free(10 * 1024**3), memory_probe=lambda: free(None))
        self.assertFalse(check(unknown_memory)['allowed'])

    def test_cancel_and_space_loss_are_distinct(self):
        event = threading.Event()
        event.set()
        guard = ResourcePreflight(cancel=event)
        with self.assertRaises(ResourceCancelled):
            guard('load', self.store, self.source.stat().st_size, files=[self.source], model=self.model)
        guard = ResourcePreflight(reserve_bytes=1, space_probe=lambda _: free(0))
        with self.assertRaises(ResourceLimitError):
            guard.check_remaining(self.root)

    def test_second_load_and_tampering(self):
        engine = MartEngine(self.store, self.guard)
        engine.load_snapshot(self.model, [self.source], {'period': '2026-01'}, [self.source.name], complete=True)
        decision = self.guard('load', self.store, self.source.stat().st_size, files=[self.source], model=self.model)
        self.assertTrue(decision['allowed'], decision)
        self.assertEqual(len(decision['input_hashes']), 1)

    def test_space_loss_during_staging_rolls_back_revision(self):
        engine = MartEngine(self.store, self.guard)
        engine.load_snapshot(self.model, [self.source], {'period': '2026-01'}, [self.source.name], complete=True)
        with self.source.open('w', encoding='utf-8', newline='') as out:
            writer = csv.writer(out)
            writer.writerow(['period', 'id', 'customer', 'name', 'amount'])
            for i in range(1001):
                writer.writerow(['2026-02', str(i), 'c', 'a', '1.00'])

        class InterruptingGuard(ResourcePreflight):
            calls = 0

            def check_remaining(self, path):
                self.calls += 1
                if self.calls >= 2:
                    raise ResourceLimitError('space lost')

        guard = InterruptingGuard(reserve_bytes=0, space_probe=lambda _: free(10 * 1024**3))
        with self.assertRaises(ResourceLimitError):
            MartEngine(self.store, guard).load_snapshot(self.model, [self.source], {'period': '2026-02'}, [self.source.name], complete=True)
        self.assertEqual(engine.catalog()['revision'], 1)
        self.assertTrue(engine.reconcile()['matches'])

    def test_multiple_volumes(self):
        guard = ResourcePreflight(reserve_bytes=0, space_probe=lambda p: free(1 if str(p).endswith('destination') else 10**10), volume_probe=lambda p: str(p))
        decision = guard('backup', self.store, 100, destination=self.root / 'destination')
        self.assertFalse(decision['allowed'])
        self.assertEqual(len(decision['volumes']), 2)

    def test_two_dimensions_composite_grain_and_partitions(self):
        dimensions = (Dimension('a', ('a0', 'a1')), Dimension('b', ('b0', 'b1')))
        model = MartModel('M', 'sales', dimensions, (Measure('m', 'm', 18, 2),),
                          'period', ('region', 'channel'), ('id0', 'id1'))
        with self.source.open('w', encoding='utf-8', newline='') as out:
            writer = csv.writer(out)
            writer.writerow(['period', 'region', 'channel', 'id0', 'id1', 'a0', 'a1', 'b0', 'b1', 'm'])
            writer.writerow(['2026-01', 'PL', 'TV', '1', '2', '한' * 41 + 'ab', 'x', 'b', 'c', '1.00'])
        engine = MartEngine(self.store, self.guard)
        scope = {'period': '2026-01', 'region': 'PL', 'channel': 'TV'}
        self.assertEqual(engine.load_snapshot(model, [self.source], scope, [self.source.name], complete=True)['rows'], 1)
        definition = ExportDefinition('group', ('a0', 'a1', 'm'), group_by=('a0', 'a1'), filters=(('b0', 'b'),))
        self.assertEqual(engine.create_export(definition)['rows'], 1)

    def test_three_dimensions_shape_and_unsupported_boundaries(self):
        dimensions = tuple(Dimension('d' + str(i), ('k' + str(i),)) for i in range(3))
        model = MartModel('M', 'sales', dimensions, (Measure('m', 'm', 18, 2),), 'period', (), ('id',))
        self.guard._shape(model)
        too_many = MartModel('M', 'sales', dimensions + (Dimension('d3', ('k3',)), Dimension('d4', ('k4',))),
                             model.measures, 'period', (), ('id',))
        with self.assertRaises(ValueError):
            self.guard._shape(too_many)

    def test_representative_profile_load_group_publish_backup(self):
        dimensions = (
            Dimension('customer', ('customer', 'shipto'), ('ca0', 'ca1', 'ca2')),
            Dimension('product', ('product',), ('pa0', 'pa1', 'pa2')),
            Dimension('division', ('division',), ('da0', 'da1', 'da2')),
            Dimension('model', ('model',), ('ma0', 'ma1', 'ma2')),
        )
        measures = tuple(Measure('m' + str(i), 'm' + str(i), 18, 2, 'USD', kind='cost') for i in range(16))
        columns = ['period', 'id', 'customer', 'shipto', 'product', 'division', 'model']
        columns += [prefix + str(i) for prefix in ('ca', 'pa', 'da', 'ma') for i in range(3)]
        columns += ['m' + str(i) for i in range(16)]
        excluded = tuple('unused' + str(i) for i in range(64 - len(columns)))
        columns += list(excluded)
        model = MartModel('M', 'sales', dimensions, measures, 'period', (), ('id',), excluded)
        with self.source.open('w', encoding='utf-8', newline='') as out:
            writer = csv.writer(out)
            writer.writerow(columns)
            for i in range(20):
                writer.writerow(['2026-01', str(i), 'c', 's', 'p', 'TV', 'm', *(['a'] * 12), *(['1.25'] * 16), *(['x'] * len(excluded))])
        engine = MartEngine(self.store, self.guard)
        loaded = engine.load_snapshot(model, [self.source], {'period': '2026-01'}, [self.source.name], complete=True)
        self.assertEqual(loaded['rows'], 20)
        definition = ExportDefinition('group', ('customer', 'm0', 'm1'), group_by=('customer',), filters=(('division', 'TV'),))
        candidate = engine.create_export(definition)
        engine.approve_export(candidate['candidate'], 'reviewer')
        engine.publish_local(candidate['candidate'], self.root / 'group.csv')
        engine.backup(self.root / 'group-backup')
        self.assertIn('25.00', (self.root / 'group.csv').read_text(encoding='utf-8-sig'))
        multi = ExportDefinition('multi', ('customer', 'shipto', 'm0'), group_by=('customer', 'shipto'), filters=(('product', 'p'),), period_start='2026-01', period_end='2026-01')
        self.assertEqual(engine.create_export(multi)['rows'], 1)
        detail = ExportDefinition('detail', ('period', 'customer', 'shipto', 'm0'), filters=(('division', 'TV'),))
        self.assertEqual(engine.create_export(detail)['rows'], 20)
        with self.source.open(encoding='utf-8', newline='') as inp:
            reader = csv.reader(inp)
            header, row = next(reader), next(reader)
        with self.source.open('w', encoding='utf-8', newline='') as out:
            writer = csv.writer(out)
            writer.writerow(header)
            for i in range(10000):
                row[header.index('customer')] = str(i)
                row[header.index('id')] = str(i)
                writer.writerow(row)
        blocked = self.guard('load', self.store, self.source.stat().st_size, files=[self.source], model=model, scope={'period': '2026-01'})
        self.assertFalse(blocked['allowed'])
        self.assertIn('10,000', blocked['reason'])


if __name__ == '__main__':
    unittest.main()
