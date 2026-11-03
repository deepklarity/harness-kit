# Good and bad tests

## A good test

Goes through the seam and asserts what the caller gets.

```typescript
test("user can checkout with valid cart", async () => {
  const cart = createCart();
  cart.add(product);
  const result = await checkout(cart, paymentMethod);
  expect(result.status).toBe("confirmed");
});
```

- Named for what the caller gets
- One logical assertion

## Implementation-coupled

The test knows how the code is built, so it breaks when the code changes and the behaviour does not.

```typescript
// Asserts on an internal collaborator
test("checkout calls paymentService.process", async () => {
  const mockPayment = vi.mock(paymentService);
  await checkout(cart, payment);
  expect(mockPayment.process).toHaveBeenCalledWith(cart.total);
});
```

```typescript
// Verifies through a side channel
test("createUser saves to database", async () => {
  await createUser({ name: "Alice" });
  const row = await db.query("SELECT * FROM users WHERE name = ?", ["Alice"]);
  expect(row).toBeDefined();
});

// Verifies through the seam
test("createUser makes user retrievable", async () => {
  const user = await createUser({ name: "Alice" });
  const retrieved = await getUser(user.id);
  expect(retrieved.name).toBe("Alice");
});
```

The same pair in pytest, for a Django endpoint:

```python
# Verifies through a side channel
def test_create_task_writes_row(client):
    client.post("/api/tasks/", {"title": "Ship it"})
    assert Task.objects.filter(title="Ship it").exists()

# Verifies through the seam
def test_created_task_is_retrievable(client):
    created = client.post("/api/tasks/", {"title": "Ship it"}).json()
    fetched = client.get(f"/api/tasks/{created['id']}/").json()
    assert fetched["title"] == "Ship it"
```

## Tautological

The expected value restates the implementation, so the test passes by construction.

```typescript
// Expected value is recomputed the way the code computes it
test("calculateTotal sums line items", () => {
  const items = [{ price: 10 }, { price: 5 }];
  const expected = items.reduce((sum, i) => sum + i.price, 0);
  expect(calculateTotal(items)).toBe(expected);
});

// Expected value is a known literal
test("calculateTotal sums line items", () => {
  expect(calculateTotal([{ price: 10 }, { price: 5 }])).toBe(15);
});
```
