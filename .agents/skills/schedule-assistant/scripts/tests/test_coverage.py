from __future__ import annotations

import copy
import unittest

import test_validate_plan as fixtures
from test_validate_plan import scheduled_plan, todo_entry, linear_goal


class CoverageTests(unittest.TestCase):
    validate = fixtures.ValidatePlanTests.validate
    codes = fixtures.ValidatePlanTests.codes

    def gap(self, result):
        return [w for w in result['warnings'] if w['code'] == 'required_coverage_gap']

    def test_due_today_without_any_entry_reports_gap_without_rejecting(self):
        plan = scheduled_plan()
        plan['task_structure']['todos'][0]['deadline'] = '2026-08-18T17:00:00+08:00'
        plan['schedule']['entries'] = []
        code, result = self.validate(plan)
        self.assertEqual(code, 0, result)
        self.assertEqual(len(self.gap(result)), 1)
        self.assertIn('missing 60 minutes', self.gap(result)[0]['message'])
        self.assertIn('2026-08-18T17:00:00+08:00', self.gap(result)[0]['message'])

    def test_partial_coverage_and_execution_window_end_report_remaining_gap(self):
        plan = scheduled_plan()
        plan['schedule']['entries'][0]['ends_at'] = '2026-08-18T10:30:00+08:00'
        code, result = self.validate(plan, {'2026-08-18': 'workday'})
        self.assertEqual(code, 0, result)
        self.assertEqual(len(self.gap(result)), 1)
        self.assertIn('missing 30 minutes', self.gap(result)[0]['message'])
        self.assertIn('execution_window', self.gap(result)[0]['message'])

    def test_current_entry_counts_only_time_after_calculation(self):
        plan = scheduled_plan()
        plan['schedule']['entries'][0].update(starts_at='2026-08-18T07:30:00+08:00', ends_at='2026-08-18T08:30:00+08:00')
        _, result = self.validate(plan)
        self.assertEqual(len(self.gap(result)), 1)
        self.assertIn('missing 30 minutes', self.gap(result)[0]['message'])

    def test_overlap_cannot_hide_missing_coverage(self):
        plan = scheduled_plan()
        plan['schedule']['entries'][0]['ends_at'] = '2026-08-18T10:30:00+08:00'
        other = copy.deepcopy(plan['schedule']['entries'][0])
        other.update(id='entry-b', daily_rank=2)
        plan['schedule']['entries'].append(other)
        code, result = self.validate(plan, {'2026-08-18': 'workday'})
        self.assertEqual(code, 1)
        self.assertIn('illegal_schedule_overlap', self.codes(result))
        self.assertEqual(len(self.gap(result)), 1)
        self.assertIn('missing 30 minutes', self.gap(result)[0]['message'])

    def test_margin_is_warning_for_both_execution_modes(self):
        for mode in ['single_block', 'splittable']:
            with self.subTest(mode=mode):
                plan = scheduled_plan()
                plan['task_structure']['todos'][0]['execution_mode'] = mode
                plan['schedule']['entries'][0]['ends_at'] = '2026-08-18T11:10:00+08:00'
                code, result = self.validate(plan, {'2026-08-18': 'workday'})
                self.assertEqual(code, 0, result)
                self.assertIn('future_coverage_exceeds_remaining', self.codes(result, 'warnings'))
                self.assertFalse(self.gap(result))

    def test_short_or_fragmented_single_block_still_rejected(self):
        for fragmented in [False, True]:
            with self.subTest(fragmented=fragmented):
                plan = scheduled_plan()
                plan['task_structure']['todos'][0]['execution_mode'] = 'single_block'
                plan['schedule']['entries'][0]['ends_at'] = '2026-08-18T10:30:00+08:00'
                if fragmented:
                    other = todo_entry('entry-b', 'todo-a', 2)
                    other.update(starts_at='2026-08-18T11:00:00+08:00', ends_at='2026-08-18T11:30:00+08:00')
                    plan['schedule']['entries'].append(other)
                code, result = self.validate(plan, {'2026-08-18': 'workday'})
                self.assertEqual(code, 1)
                self.assertIn('invalid_single_block_coverage', self.codes(result))

    def test_margin_does_not_excuse_deadline_violation(self):
        plan = scheduled_plan()
        todo = plan['task_structure']['todos'][0]
        todo.update(execution_mode='single_block', deadline='2026-08-18T11:00:00+08:00')
        plan['schedule']['entries'][0]['ends_at'] = '2026-08-18T11:10:00+08:00'
        code, result = self.validate(plan, {'2026-08-18': 'workday'})
        self.assertEqual(code, 1)
        self.assertIn('todo_after_deadline', self.codes(result))

    def test_closed_paused_remote_or_unplanned_scope_has_no_required_gap(self):
        for state in ['completed', 'cancelled', 'paused', 'later_week', 'unplanned', 'stale_week']:
            with self.subTest(state=state):
                plan = scheduled_plan()
                todo = plan['task_structure']['todos'][0]
                plan['schedule']['entries'] = []
                if state in ['completed', 'cancelled']:
                    todo.update(status=state, remaining_estimate_minutes=0)
                elif state == 'paused':
                    goal = linear_goal('goal-a', 'P1');goal['status'] = 'paused'
                    plan['task_structure']['goals'] = [goal]
                    todo['parent_refs'] = [dict(goal_id='goal-a', node_id=None, habit_period_starts_on=None, habit_occurrence_index=None)]
                elif state == 'later_week':
                    todo['execution_window']['ends_at'] = '2026-08-30T18:00:00+08:00'
                    todo['deadline'] = '2026-08-29T17:00:00+08:00'
                elif state == 'unplanned':
                    plan['schedule'].update(planning_window=None, generated_at=None)
                else:
                    plan['schedule']['planning_window'] = dict(starts_on='2026-08-10', ends_on='2026-08-16')
                code, result = self.validate(plan)
                self.assertEqual(code, 0, result)
                self.assertFalse(self.gap(result))

    def test_shared_active_parent_deadline_still_requires_coverage(self):
        plan = scheduled_plan();plan['schedule']['entries'] = []
        paused = linear_goal('paused', 'P1');paused['status'] = 'paused'
        active = linear_goal('active', 'P1');active['deadline'] = '2026-08-18T17:00:00+08:00'
        plan['task_structure']['goals'] = [paused, active]
        todo = plan['task_structure']['todos'][0]
        todo['execution_window']['ends_at'] = '2026-08-30T18:00:00+08:00'
        todo['parent_refs'] = [dict(goal_id=g['id'], node_id=None, habit_period_starts_on=None, habit_occurrence_index=None) for g in [paused, active]]
        code, result = self.validate(plan)
        self.assertEqual(code, 0, result)
        self.assertEqual(len(self.gap(result)), 1)
        self.assertIn('goal deadline', self.gap(result)[0]['message'])

    def test_blocked_dependency_gap_is_advisory_and_not_readiness_claim(self):
        plan = scheduled_plan();plan['schedule']['entries'] = []
        predecessor = copy.deepcopy(plan['task_structure']['todos'][0]);predecessor['id'] = 'todo-before'
        plan['task_structure']['todos'][0]['depends_on_todo_ids'] = ['todo-before']
        plan['task_structure']['todos'].append(predecessor)
        code, result = self.validate(plan)
        self.assertEqual(code, 0, result)
        self.assertEqual(len(self.gap(result)), 2)
        self.assertTrue(all('progress and dependencies' in w['message'] for w in self.gap(result)))
