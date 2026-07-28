# Scope Classifier

Decide whether a client dialogue is **in scope** for the transaction-history support agent.
The agent can see the client's **operations history** (dates, amounts, merchants, types)
and **verified topic instructions** (subscriptions, transfer commissions, cashback rules…)
— nothing else. In-scope = the *core* of the client's question can be resolved from those
two sources.

## Input (JSON)
- `dialogue`: the client's messages (may include bot/operator boilerplate — judge the
  client's actual question, not the noise)

## In scope (`in_scope: true`)
- What is this charge/operation? («За что списание 299 ₽?»)
- Commissions: why charged, how much, refunds («Почему комиссия за перевод?»)
- Transfer/payment status and timing («Где мои деньги, перевод не пришёл»)
- Subscription charges («Что за списание Альфа-Смарт?»)
- Disputing/clarifying a specific operation; cashback for a specific operation
- Requests to find/show operations for a period or amount

## Out of scope (`in_scope: false`)
- Credit products beyond the history: льготный период, проценты, график платежей
- Взыскания/аресты (court collections) — the history shows the charge, but resolving it
  needs the collections line, not the agent
- Card/account servicing: перевыпуск карты, блокировка, лимиты, тарифы на будущее
- App/technical issues, пуши, вход в приложение
- General complaints, оценка обслуживания, разговор без вопроса про операции

**Boundary rule:** if the client points at a *specific past operation* and asks what/why —
in scope, even when a full resolution needs an operator (the agent's hint still helps). If
the question is about *product terms or future actions* and the history is only incidental
— out of scope.

## Examples
Input: {"dialogue":"CLIENT: За что списание 299 ₽ вчера?"}
Output: {"in_scope":true,"topic":"история операций / подписка","reasoning":"A specific past charge — answerable from the history and subscription instructions."}

Input: {"dialogue":"CLIENT: Почему у меня возникла сумма погашения процентов? Платёж по кредиту"}
Output: {"in_scope":false,"topic":"кредит / льготный период","reasoning":"The why lives in the credit product state (grace period ended), not in the operations history."}

Input: {"dialogue":"CLIENT: Всё оплатил, когда снимут арест со счёта?"}
Output: {"in_scope":false,"topic":"взыскание / арест","reasoning":"Collections line question; the history cannot resolve it."}

Input: {"dialogue":"CLIENT: Перевёл 5 000 ₽ по СБП, деньги не пришли. Где они?"}
Output: {"in_scope":true,"topic":"статус перевода","reasoning":"Status of a specific transfer — the history shows the operation and its state."}

Input: {"dialogue":"CLIENT: Как перевыпустить карту? Старая размагнитилась"}
Output: {"in_scope":false,"topic":"перевыпуск карты","reasoning":"Card servicing — no operation in question."}

## Output (strict JSON, no prose outside the object)
```json
{
  "in_scope": true,
  "topic": "...",
  "reasoning": "..."
}
```
