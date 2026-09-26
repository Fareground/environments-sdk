"""A model's partial turn is observed evidence, not a finished decision."""
from types import SimpleNamespace as NS

import pytest

import fg_env

CASE = {
    'name': 'A partially completed shopping decision', 'clock': {'rounds': 1},
    'types': {'buyer': {'agent': True, 'props': {'units': 0}}},
    'entities': {'shopper': {'type': 'buyer'}},
    'stages': [{'name': 'shopping', 'max_actions': 3}],
    'actions': {'buy': {'by': 'buyer', 'do': ['$actor.units += 1']}},
    'outputs': {'units': {'expr': '$entity(shopper).units', 'primary': True}},
}


class Client:
    def __init__(self, finish):
        self.finish = finish
        self.calls = 0
        self.chat = NS(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        name = 'buy' if self.calls == 1 else 'end_turn' if self.finish else 'buy'
        message = NS(content='', tool_calls=[NS(id=f'call{self.calls}',
                     function=NS(name=name, arguments='{}'))])
        return NS(choices=[NS(message=message, finish_reason='tool_calls')],
                  usage=NS(prompt_tokens=1, completion_tokens=1))


@pytest.mark.parametrize('finish', [False, True])
@pytest.mark.parametrize('audience', ['owner', 'analyst'])
def test_partial_turn_cutoff_is_disclosed_without_discarding_its_outcome(finish, audience):
    client = Client(finish)
    participant = fg_env.participants.openai(client, 'test', max_steps=2)
    result = fg_env.run(CASE, participant, seed=1)
    assert client.calls == 2
    assert result.status == 'completed' and result.outputs['units'] == (1 if finish else 2)
    assert result.stats['out_of_steps'] == (0 if finish else 1)
    report = fg_env.analysis.report(result, audience, contract=CASE, objective='max:units')
    if finish:
        assert result.ok and report.recommendation is not None
        assert report.sections[0].title != 'Execution health'
    else:
        assert not result.ok and 'out_of_steps' in result.degraded
        assert report.recommendation is None
        assert report.sections[0].title == 'Execution health'
        assert 'out_of_steps' in report.markdown
        table = report.sections[0].tables[0]
        assert table.columns[-1] == 'Call-limit cutoffs'
        assert table.rows[0][-1] == '1'
        assert table.rows[0][2] == '1'
        assert result.stats['invalid_calls'] == 0
