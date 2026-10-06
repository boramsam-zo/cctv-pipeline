import test from 'node:test';
import assert from 'node:assert/strict';
import {filterCases, pageCases} from '../src/selectors.mjs';
const rows = [
  {id:'TEST-001',title:'충돌',location:'교차로 A',kind:'충돌',model:'X3D-S',state:'검토 대기'},
  {id:'TEST-002',title:'정차',location:'도로 B',kind:'차량 단독',model:'R2',state:'자료 확인 필요'},
];
test('search combines with model and status filters', () => {
  assert.deepEqual(filterCases(rows, ' 교차로 ', 'X3D-S', '검토 대기'), [rows[0]]);
  assert.deepEqual(filterCases(rows, 'TEST-002', 'X3D-S', '전체'), []);
  assert.deepEqual(filterCases(rows, 'test-002', '전체', '전체'), [rows[1]]);
});
test('empty results and shrinking filters keep pagination valid', () => {
  assert.deepEqual(pageCases([], 4, 3), {items:[],current:1,count:1});
  assert.deepEqual(pageCases(rows, 5, 1), {items:[rows[1]],current:2,count:2});
});
