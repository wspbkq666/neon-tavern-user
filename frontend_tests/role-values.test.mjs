import test from 'node:test';
import assert from 'node:assert/strict';
import {displayRoleValue,readRoleValue,mergeRoleRelationships} from '../frontend_dist/assets/role-values.js';

test('未修改的数字、对象和原文空格均保持原始类型与内容',()=>{
  for(const value of [5,{等级:2},' 原文 ',true,null]) {
    assert.deepEqual(readRoleValue(value,displayRoleValue(value)),value);
  }
});
test('修改状态后可以明确保存新的数值或中文文本',()=>{
  assert.equal(readRoleValue(5,'4'),4);
  assert.equal(readRoleValue('平静','疲惫'),'疲惫');
});
test('关系编辑保留其他中文键，未修改的对象关系不转为字符串',()=>{
  const original={盟约:'永久',关系:{阶段:2},称呼:'阁下'};
  assert.deepEqual(mergeRoleRelationships(original,{关系:'{"阶段":2}',称呼:'先生'}),{盟约:'永久',关系:{阶段:2},称呼:'先生'});
});
