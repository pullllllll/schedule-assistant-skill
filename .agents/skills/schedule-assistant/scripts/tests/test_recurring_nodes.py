"""Flexible recurring goals, outcome nodes and stable instance identity."""
import copy
import unittest
from datetime import datetime

from test_validate_plan import empty_plan, linear_goal, base_todo, scheduled_plan, todo_entry
import test_apply_plan as apply_fixtures
from validate_plan import Validator
from apply_plan import requires_incremental_discovery
from daily_ranking import derive_daily_rankings

NOW = datetime.fromisoformat('2026-08-18T08:00:00+08:00')
STAMP = '2026-08-18T07:00:00+08:00'


def goal():
    return dict(linear_goal('goal-a', 'P1'), type='recurring')


def node(node_id='node-a', kind='cycle', anchor=None):
    return dict(id=node_id, goal_id='goal-a', title='可验收的成果', outcome='交付已完成并确认',
                kind=kind, cycle_starts_on=anchor, status='pending', starts_on=None, deadline=None,
                depends_on_node_ids=[], context=None, created_at=STAMP, updated_at=STAMP)


def template():
    return {'node': {'title': '当期交付', 'outcome': '已发布', 'context': None},
            'todos': [{'template_key': 'publish', 'title': '发布', 'estimate_minutes': 60,
                       'execution_mode': 'single_block', 'intensity': 'medium',
                       'depends_on_template_keys': [], 'context': None}]}


def rule():
    return dict(interval=2, unit='week', first_cycle_starts_on='2026-08-17', first_deadline=None)


def plan_with_nodes(*nodes):
    plan = empty_plan()
    plan['task_structure'].update(goals=[goal()], nodes=list(nodes))
    return plan


class RecurringNodeTests(unittest.TestCase):
    def check(self, plan):
        return Validator(plan, NOW, {'2026-08-18': 'workday'}).validate()

    def valid(self, plan):
        result = self.check(plan)
        self.assertTrue(result['ok'], result)

    def error(self, plan, code):
        self.assertIn(code, {e['code'] for e in self.check(plan)['errors']})

    def test_unknown_rules_allow_current_cycle_regular_and_direct_work(self):
        plan = plan_with_nodes(node(), node('equipment', 'regular'))
        for name, parent in [('episode', 'node-a'), ('setup', 'equipment'), ('reply', None)]:
            todo = base_todo(name)
            todo['parent_refs'] = [dict(goal_id='goal-a', node_id=parent,
                                       habit_period_starts_on=None, habit_occurrence_index=None)]
            plan['task_structure']['todos'].append(todo)
        self.valid(plan)

    def test_partial_rules_retain_known_facts_and_deadline_optional(self):
        for recurrence in [None, dict(rule(), first_cycle_starts_on=None),
                           dict(rule(), interval=None, unit=None), rule()]:
            with self.subTest(recurrence=recurrence):
                plan = plan_with_nodes(node())
                plan['task_structure']['goals'][0].update(recurrence=recurrence, cycle_template=template())
                self.valid(plan)

    def test_incomplete_frequency_pair_and_empty_template_rejected(self):
        plan = plan_with_nodes()
        plan['task_structure']['goals'][0]['recurrence'] = dict(rule(), unit=None)
        self.error(plan, 'incomplete_cadence')
        plan['task_structure']['goals'][0].update(recurrence=None, cycle_template=dict(template(), todos=[]))
        self.error(plan, 'empty_cycle_template')

    def test_invalid_rule_values_and_deadline_rejected(self):
        for changed, code in [({'interval': 0}, 'invalid_integer'), ({'unit': 'year'}, 'invalid_enum'),
                              ({'first_cycle_starts_on': 'bad'}, 'invalid_date'),
                              ({'first_deadline': '2026-08-16T20:00:00+08:00'}, 'invalid_cycle_deadline')]:
            with self.subTest(changed=changed):
                plan = plan_with_nodes()
                plan['task_structure']['goals'][0]['recurrence'] = dict(rule(), **changed)
                self.error(plan, code)

    def test_linear_regular_node_and_direct_task_supported(self):
        plan = plan_with_nodes(node(kind='regular'))
        plan['task_structure']['goals'][0]['type'] = 'linear'
        self.valid(plan)
        plan['task_structure']['nodes'][0]['kind'] = 'cycle'
        self.error(plan, 'invalid_node_goal')

    def test_regular_node_cannot_claim_period_and_partial_fields_are_not_legacy(self):
        plan = plan_with_nodes(node(kind='regular', anchor='2026-08-17'))
        self.error(plan, 'invalid_node_cycle')
        del plan['task_structure']['nodes'][0]['cycle_starts_on']
        self.error(plan, 'missing_field')

    def test_period_dedup_uses_identity_not_actual_start_including_cancelled(self):
        plan = plan_with_nodes(node(anchor='2026-08-17'), node('second', anchor='2026-08-17'))
        plan['task_structure']['nodes'][0].update(status='cancelled', starts_on='2026-08-20')
        self.error(plan, 'duplicate_recurring_node')
        plan['task_structure']['nodes'][1]['cycle_starts_on'] = '2026-08-31'
        self.valid(plan)

    def test_legacy_read_only_and_duplicate_against_new_format(self):
        old = node(anchor='2026-08-17')
        del old['kind']; del old['cycle_starts_on']
        old.update(starts_on='2026-08-17', deadline='2026-08-23T20:00:00+08:00')
        plan = plan_with_nodes(old)
        before = copy.deepcopy(plan)
        self.valid(plan)
        self.assertEqual(plan, before)
        plan['task_structure']['nodes'].append(node('new', anchor='2026-08-17'))
        self.error(plan, 'duplicate_recurring_node')

    def test_actual_start_and_cross_type_dependency_remain_effective(self):
        plan = scheduled_plan()
        plan['task_structure'].update(goals=[goal()], nodes=[node(), node('equipment', 'regular')])
        todo = plan['task_structure']['todos'][0]
        todo['parent_refs'] = [dict(goal_id='goal-a', node_id='node-a', habit_period_starts_on=None, habit_occurrence_index=None)]
        plan['task_structure']['nodes'][0]['depends_on_node_ids'] = ['equipment']
        # An unfulfilled outcome blocks the episode, even with no listed setup actions.
        self.assertFalse(self.check(plan)['ok'])
        plan['task_structure']['nodes'][1]['status'] = 'completed'
        self.valid(plan)
        plan['task_structure']['nodes'][0]['starts_on'] = '2026-08-19'
        self.assertFalse(self.check(plan)['ok'])

    def test_node_predecessor_can_be_planned_before_successor_without_marking_complete(self):
        plan = scheduled_plan()
        plan['task_structure'].update(goals=[goal()], nodes=[node(), node('equipment', 'regular')])
        plan['task_structure']['nodes'][0]['depends_on_node_ids'] = ['equipment']
        action = plan['task_structure']['todos'][0]
        action['deadline'] = '2026-08-18T12:00:00+08:00'
        action['parent_refs'] = [dict(goal_id='goal-a', node_id='node-a', habit_period_starts_on=None, habit_occurrence_index=None)]
        setup = base_todo('setup')
        setup['parent_refs'] = [dict(goal_id='goal-a', node_id='equipment', habit_period_starts_on=None, habit_occurrence_index=None)]
        plan['task_structure']['todos'].append(setup)
        setup_entry = todo_entry('setup-entry', 'setup')
        setup_entry.update(starts_at='2026-08-18T09:00:00+08:00', ends_at='2026-08-18T10:00:00+08:00')
        plan['schedule']['entries'].append(setup_entry)
        before = copy.deepcopy(plan)
        rankings = derive_daily_rankings(plan, NOW)
        self.assertEqual(plan, before)
        self.assertLess(rankings['assignments']['setup-entry']['daily_rank'], rankings['assignments']['entry-a']['daily_rank'])
        for entry in plan['schedule']['entries']:
            entry.update(rankings['assignments'][entry['id']])
        self.valid(plan)
        self.assertEqual(plan['task_structure']['nodes'][1]['status'], 'pending')
        # All listed actions done is insufficient evidence for the node outcome.
        setup.update(status='completed', remaining_estimate_minutes=0)
        plan['schedule']['entries'].remove(setup_entry)
        self.error(plan, 'node_dependency_completion_unknown')

    def test_goal_configuration_changes_and_node_binding_require_discovery(self):
        current = plan_with_nodes(node())
        for field, value in [('type', 'linear'), ('recurrence', rule()), ('cycle_template', template())]:
            candidate = copy.deepcopy(current)
            candidate['task_structure']['goals'][0][field] = value
            self.assertTrue(requires_incremental_discovery(current, candidate))
        candidate = copy.deepcopy(current)
        candidate['task_structure']['nodes'][0]['cycle_starts_on'] = '2026-08-17'
        self.assertTrue(requires_incremental_discovery(current, candidate))

    def test_binding_and_rule_activation_commit_preserve_existing_work(self):
        current = scheduled_plan()
        current['task_structure'].update(goals=[goal()], nodes=[node()])
        current['task_structure']['todos'][0]['parent_refs'] = [dict(goal_id='goal-a', node_id='node-a', habit_period_starts_on=None, habit_occurrence_index=None)]
        candidate = copy.deepcopy(current)
        candidate['task_structure']['goals'][0].update(recurrence=rule(), cycle_template=template(), updated_at=NOW.isoformat())
        candidate['task_structure']['nodes'][0].update(cycle_starts_on='2026-08-17', updated_at=NOW.isoformat())
        harness = apply_fixtures.ApplyPlanTests()
        denied, _, _, _ = harness.run_apply(candidate, current, classifications={'2026-08-18': 'workday'})
        self.assertNotEqual(denied.returncode, 0)
        result, saved, _, _ = harness.run_apply(candidate, current, classifications={'2026-08-18': 'workday'}, discovery_state=harness.incremental_discovery_state())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(saved['schedule'], current['schedule'])
        self.assertEqual(saved['task_structure']['todos'], current['task_structure']['todos'])
        self.assertEqual(len(saved['task_structure']['nodes']), 1)

    def test_bound_identity_cannot_move_but_actual_start_can(self):
        current = plan_with_nodes(node(anchor='2026-08-17'))
        harness = apply_fixtures.ApplyPlanTests()
        candidate = copy.deepcopy(current)
        candidate['task_structure']['nodes'][0].update(starts_on='2026-08-20', updated_at=NOW.isoformat())
        result, _, _, _ = harness.run_apply(candidate, current)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for changes in [dict(cycle_starts_on='2026-08-31'), dict(kind='regular', cycle_starts_on=None), dict(cycle_starts_on=None)]:
            candidate = copy.deepcopy(current)
            candidate['task_structure']['nodes'][0].update(**changes, updated_at=NOW.isoformat())
            result, saved, _, _ = harness.run_apply(candidate, current, discovery_state=harness.incremental_discovery_state())
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('cycle_identity_changed', result.stdout)
            self.assertEqual(saved, current)

    def test_legacy_explicit_fields_upgrade_does_not_trigger_discovery(self):
        old = node()
        del old['kind']; del old['cycle_starts_on']
        old.update(starts_on='2026-08-17', deadline='2026-08-23T20:00:00+08:00')
        current = plan_with_nodes(old)
        candidate = copy.deepcopy(current)
        candidate['task_structure']['nodes'][0].update(kind='cycle', cycle_starts_on='2026-08-17', starts_on='2026-08-20', updated_at=NOW.isoformat())
        self.assertFalse(requires_incremental_discovery(current, candidate))
        result, saved, _, _ = apply_fixtures.ApplyPlanTests().run_apply(candidate, current)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(saved['task_structure']['nodes'][0]['id'], old['id'])


if __name__ == '__main__':
    unittest.main()
