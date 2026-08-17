import { BookingList } from "@/components/BookingList";

/**
 * "My requests".
 *
 * A client component does the work because the list is scoped by a session token
 * held in localStorage — there is no account to render this on the server for.
 */
export default function BookingsPage() {
  return (
    <main>
      <BookingList />
    </main>
  );
}
