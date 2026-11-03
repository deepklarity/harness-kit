# When to mock

Mock at system boundaries: external APIs (payment, email), time and randomness, the file system, and a database when a test database is not practical. Your own modules and internal collaborators run for real.

## Designing a boundary so it mocks cleanly

**Pass external dependencies in** rather than creating them inside:

```typescript
// Easy to mock
function processPayment(order, paymentClient) {
  return paymentClient.charge(order.total);
}

// Hard to mock
function processPayment(order) {
  const client = new StripeClient(process.env.STRIPE_KEY);
  return client.charge(order.total);
}
```

**One function per external operation** rather than one generic fetcher, so each mock returns one shape and a test shows which operations it exercises:

```typescript
// Each function is independently mockable
const api = {
  getUser: (id) => fetch(`/users/${id}`),
  getOrders: (userId) => fetch(`/users/${userId}/orders`),
  createOrder: (data) => fetch('/orders', { method: 'POST', body: data }),
};

// The mock needs conditional logic to answer
const api = {
  fetch: (endpoint, options) => fetch(endpoint, options),
};
```
