import { Fragment, StrictMode, useEffect, useState, type FormEvent } from "react"
import { createRoot } from "react-dom/client"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table"

type Person = { email: string; name: string; username: string; groups: string[]; is_approver: boolean; categories: string[] }
type Status = "pendente" | "aprovada" | "recusada"
type Expense = {
  id: string; requester_email: string; requester_name: string; expense_date: string; description: string
  category: string; amount_cents: number; notes: string | null; status: Status
  decided_by_email: string | null; decided_by_name: string | null; decided_at: string | null
  decision_note: string | null; created_at: string
}

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" })
const brl = (cents: number) => money.format(cents / 100)
// expense_date is a calendar date (YYYY-MM-DD); format it without a timezone shift.
const day = (iso: string) => iso.split("-").reverse().join("/")
const stamp = (iso: string) => new Date(iso).toLocaleDateString("pt-BR")
const localToday = () => {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`
}

/** "1.234,56", "1234,56", "1234.56", "R$ 45" → centavos; null when not a valid amount. */
function toCents(text: string): number | null {
  let value = text.replace(/R\$|\s/g, "")
  if (value.includes(",")) value = value.replace(/\./g, "").replace(",", ".")
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(value)
  if (!match) return null
  const cents = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"))
  return cents > 0 ? cents : null
}

const STATUS_LABEL: Record<Status, string> = { pendente: "Pendente", aprovada: "Aprovada", recusada: "Recusada" }
const STATUS_VARIANT: Record<Status, "secondary" | "default" | "destructive"> = { pendente: "secondary", aprovada: "default", recusada: "destructive" }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    if (response.status === 401) throw new Error("Entre com a sua conta da empresa.")
    let message = "Não foi possível concluir. Tente de novo."
    try {
      const detail = (await response.json()).detail
      if (typeof detail === "string") message = detail
      else if (Array.isArray(detail) && detail[0]?.msg) message = String(detail[0].msg).replace(/^Value error, /, "")
    } catch { /* keep the generic message */ }
    throw new Error(message)
  }
  return response.status === 204 ? undefined as T : response.json()
}
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })

function StatusBadge({ status }: { status: Status }) {
  return <Badge variant={STATUS_VARIANT[status]}>{STATUS_LABEL[status]}</Badge>
}

function NewExpense({ categories, onCreated }: { categories: string[]; onCreated: () => Promise<void> }) {
  const empty = { expense_date: localToday(), description: "", category: "", amount: "", notes: "" }
  const [form, setForm] = useState(empty)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [done, setDone] = useState("")
  const set = (field: keyof typeof empty) => (value: string) => setForm(current => ({ ...current, [field]: value }))
  const cents = toCents(form.amount)
  const ready = form.expense_date && form.description.trim() && form.category && cents !== null

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!ready) return
    setBusy(true); setError(""); setDone("")
    try {
      await api<Expense>("/api/expenses", json("POST", {
        expense_date: form.expense_date, description: form.description.trim(), category: form.category,
        amount_cents: cents, notes: form.notes.trim() || null,
      }))
      setForm(empty); setDone("Despesa enviada para aprovação."); await onCreated()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return <Card>
    <CardHeader><CardTitle>Nova despesa</CardTitle><CardDescription>Registre o que você pagou. O gestor aprova ou recusa e você acompanha o status logo abaixo.</CardDescription></CardHeader>
    <CardContent>
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor="expense-date">Data</Label>
          <Input id="expense-date" type="date" value={form.expense_date} max={localToday()} onChange={e => set("expense_date")(e.target.value)} required disabled={busy} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="expense-category">Categoria</Label>
          <Select value={form.category} onValueChange={set("category")} disabled={busy}>
            <SelectTrigger id="expense-category"><SelectValue placeholder="Escolha a categoria" /></SelectTrigger>
            <SelectContent>{categories.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="expense-description">Descrição</Label>
          <Input id="expense-description" value={form.description} maxLength={200} placeholder="Ex.: Uber até o cliente" onChange={e => set("description")(e.target.value)} required disabled={busy} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="expense-amount">Valor (R$)</Label>
          <Input id="expense-amount" inputMode="decimal" value={form.amount} placeholder="0,00" onChange={e => set("amount")(e.target.value)}
            aria-invalid={form.amount !== "" && cents === null} required disabled={busy} />
          {form.amount !== "" && cents === null && <p className="text-xs text-destructive">Use um valor como 45,90.</p>}
        </div>
        <div className="grid gap-2 sm:col-span-2">
          <Label htmlFor="expense-notes">Observação <span className="font-normal text-muted-foreground">(opcional)</span></Label>
          <Textarea id="expense-notes" value={form.notes} maxLength={1000} placeholder="Contexto para o gestor: cliente, projeto, por que foi necessário…" onChange={e => set("notes")(e.target.value)} disabled={busy} />
        </div>
        <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
          <Button type="submit" disabled={busy || !ready}>{busy ? "Enviando…" : "Enviar para aprovação"}</Button>
          {done && <p role="status" className="text-sm text-muted-foreground">{done}</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        </div>
      </form>
    </CardContent>
  </Card>
}

function MyExpenses({ expenses, loading, onChanged }: { expenses: Expense[]; loading: boolean; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")
  const total = (status: Status) => expenses.filter(e => e.status === status).reduce((sum, e) => sum + e.amount_cents, 0)

  async function cancel(expense: Expense) {
    if (!window.confirm(`Cancelar a despesa "${expense.description}"?`)) return
    setBusy(expense.id); setError("")
    try { await api<void>(`/api/expenses/${expense.id}`, { method: "DELETE" }); await onChanged() }
    catch (e) { setError((e as Error).message) } finally { setBusy("") }
  }

  return <Card>
    <CardHeader>
      <CardTitle>Minhas despesas</CardTitle>
      <CardDescription>Pendente {brl(total("pendente"))} · Aprovado {brl(total("aprovada"))}</CardDescription>
    </CardHeader>
    <CardContent className="space-y-4">
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Table>
        <TableHeader><TableRow>
          <TableHead>Data</TableHead><TableHead>Descrição</TableHead><TableHead>Categoria</TableHead>
          <TableHead className="text-right">Valor</TableHead><TableHead>Status</TableHead><TableHead><span className="sr-only">Ações</span></TableHead>
        </TableRow></TableHeader>
        <TableBody>{expenses.map(expense => <TableRow key={expense.id}>
          <TableCell className="tabular-nums">{day(expense.expense_date)}</TableCell>
          <TableCell className="whitespace-normal">
            <p className="font-medium">{expense.description}</p>
            {expense.notes && <p className="text-xs text-muted-foreground">{expense.notes}</p>}
          </TableCell>
          <TableCell>{expense.category}</TableCell>
          <TableCell className="text-right tabular-nums">{brl(expense.amount_cents)}</TableCell>
          <TableCell className="whitespace-normal">
            <StatusBadge status={expense.status} />
            {expense.decided_at && <p className="mt-1 text-xs text-muted-foreground">
              por {expense.decided_by_name || expense.decided_by_email} em {stamp(expense.decided_at)}
            </p>}
            {expense.decision_note && <p className="text-xs text-muted-foreground">Motivo: {expense.decision_note}</p>}
          </TableCell>
          <TableCell className="text-right">
            {expense.status === "pendente" && <Button variant="ghost" size="sm" onClick={() => cancel(expense)} disabled={busy === expense.id}
              aria-label={`Cancelar ${expense.description}`}>Cancelar</Button>}
          </TableCell>
        </TableRow>)}</TableBody>
      </Table>
      {!expenses.length && <p className="py-6 text-center text-sm text-muted-foreground" role="status">{loading ? "Carregando…" : "Nenhuma despesa ainda. Registre a primeira acima."}</p>}
    </CardContent>
  </Card>
}

function Approvals({ person, expenses, onChanged }: { person: Person; expenses: Expense[]; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")
  const [declining, setDeclining] = useState("")
  const [reason, setReason] = useState("")
  const total = expenses.reduce((sum, e) => sum + e.amount_cents, 0)

  async function decide(expense: Expense, decision: "aprovada" | "recusada", note?: string) {
    setBusy(expense.id); setError("")
    try {
      await api<Expense>(`/api/approvals/${expense.id}`, json("POST", { decision, note: note ?? null }))
      setDeclining(""); setReason(""); await onChanged()
    } catch (e) { setError((e as Error).message) } finally { setBusy("") }
  }

  return <Card>
    <CardHeader>
      <CardTitle>Aprovações pendentes</CardTitle>
      <CardDescription>{expenses.length ? `${expenses.length} despesa(s) aguardando · ${brl(total)}` : "Você é gestor: as despesas da equipe aparecem aqui para aprovar ou recusar."}</CardDescription>
    </CardHeader>
    <CardContent className="space-y-4">
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Table>
        <TableHeader><TableRow>
          <TableHead>Solicitante</TableHead><TableHead>Data</TableHead><TableHead>Descrição</TableHead><TableHead>Categoria</TableHead>
          <TableHead className="text-right">Valor</TableHead><TableHead><span className="sr-only">Ações</span></TableHead>
        </TableRow></TableHeader>
        <TableBody>{expenses.map(expense => {
          const own = expense.requester_email.toLowerCase() === person.email.toLowerCase()
          return <Fragment key={expense.id}>
            <TableRow>
              <TableCell className="whitespace-normal">
                <p className="font-medium">{expense.requester_name}</p>
                <p className="text-xs text-muted-foreground">{expense.requester_email}</p>
              </TableCell>
              <TableCell className="tabular-nums">{day(expense.expense_date)}</TableCell>
              <TableCell className="whitespace-normal">
                <p className="font-medium">{expense.description}</p>
                {expense.notes && <p className="text-xs text-muted-foreground">{expense.notes}</p>}
              </TableCell>
              <TableCell>{expense.category}</TableCell>
              <TableCell className="text-right tabular-nums">{brl(expense.amount_cents)}</TableCell>
              <TableCell className="text-right">
                {own ? <span className="text-xs text-muted-foreground">Sua despesa: outro gestor decide</span> :
                  <div className="flex justify-end gap-2">
                    <Button size="sm" onClick={() => decide(expense, "aprovada")} disabled={!!busy}>Aprovar</Button>
                    <Button size="sm" variant="outline" onClick={() => { setDeclining(expense.id); setReason("") }} disabled={!!busy}>Recusar</Button>
                  </div>}
              </TableCell>
            </TableRow>
            {declining === expense.id && <TableRow className="hover:bg-transparent">
              <TableCell colSpan={6} className="whitespace-normal">
                <form className="grid gap-2 py-2" onSubmit={e => { e.preventDefault(); if (reason.trim()) decide(expense, "recusada", reason.trim()) }}>
                  <Label htmlFor={`reason-${expense.id}`}>Motivo da recusa</Label>
                  <Textarea id={`reason-${expense.id}`} value={reason} maxLength={1000} autoFocus
                    placeholder="Quem pediu vai ver este motivo." onChange={e => setReason(e.target.value)} disabled={!!busy} />
                  <div className="flex gap-2">
                    <Button type="submit" size="sm" variant="destructive" disabled={!!busy || !reason.trim()}>Confirmar recusa</Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => setDeclining("")} disabled={!!busy}>Voltar</Button>
                  </div>
                </form>
              </TableCell>
            </TableRow>}
          </Fragment>
        })}</TableBody>
      </Table>
      {!expenses.length && <p className="py-6 text-center text-sm text-muted-foreground" role="status">Nada pendente.</p>}
    </CardContent>
  </Card>
}

function App() {
  const [person, setPerson] = useState<Person | null>(null)
  const [mine, setMine] = useState<Expense[]>([])
  const [queue, setQueue] = useState<Expense[]>([])
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)

  async function refresh(me: Person | null = person) {
    const [own, pending] = await Promise.all([
      api<{ expenses: Expense[] }>("/api/expenses"),
      me?.is_approver ? api<{ expenses: Expense[] }>("/api/approvals") : Promise.resolve({ expenses: [] }),
    ])
    setMine(own.expenses); setQueue(pending.expenses)
  }
  useEffect(() => {
    api<Person>("/api/me").then(async me => { setPerson(me); await refresh(me) })
      .catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  return <main className="mx-auto max-w-5xl space-y-8 px-4 py-12 md:px-8">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><h1 className="text-3xl font-semibold tracking-tight">Reembolso de despesas</h1><p className="mt-2 text-muted-foreground">Registre, acompanhe e aprove reembolsos.</p></div>
      {person && <div className="space-y-1 text-right"><p className="text-sm font-medium">{person.name || person.username || person.email}</p>
        <div className="flex justify-end gap-1"><Badge variant="secondary">{person.email}</Badge>{person.is_approver && <Badge variant="outline">Gestor</Badge>}</div></div>}
    </header>
    {error && <p role="alert" className="rounded-lg border border-destructive p-4 text-sm text-destructive">{error}</p>}
    {person && <NewExpense categories={person.categories} onCreated={() => refresh()} />}
    {person?.is_approver && <Approvals person={person} expenses={queue} onChanged={() => refresh()} />}
    <MyExpenses expenses={mine} loading={loading} onChanged={() => refresh()} />
  </main>
}
createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>)
