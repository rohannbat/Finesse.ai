import SwiftUI

@MainActor
final class CoachViewModel: ObservableObject {
    @Published var messages: [CoachMessage] = []
    @Published var draft = ""
    @Published var isSending = false
    @Published var errorMessage: String?

    func loadHistory() async {
        if let response = try? await APIClient.shared.coachHistory() {
            messages = response.messages
        }
    }

    func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending else { return }
        draft = ""
        errorMessage = nil
        isSending = true
        defer { isSending = false }

        // Optimistic local echo of the user's message
        messages.append(CoachMessage(id: UUID().uuidString, role: "user", content: text, createdAt: ""))
        do {
            let response = try await APIClient.shared.coachChat(message: text)
            messages.append(response.reply)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct CoachView: View {
    @StateObject private var model = CoachViewModel()
    @FocusState private var inputFocused: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 10) {
                            if model.messages.isEmpty {
                                emptyState
                            }
                            ForEach(model.messages) { message in
                                bubble(message)
                            }
                            if model.isSending {
                                HStack {
                                    ProgressView()
                                    Text("Coach is thinking…")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    Spacer()
                                }
                                .padding(.horizontal)
                            }
                            if let error = model.errorMessage {
                                Text(error)
                                    .font(.caption)
                                    .foregroundStyle(.red)
                            }
                        }
                        .padding(.vertical)
                    }
                    .onChange(of: model.messages.count) { _, _ in
                        if let last = model.messages.last {
                            withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                        }
                    }
                }
                inputBar
            }
            .navigationTitle("Coach")
            .navigationBarTitleDisplayMode(.inline)
            .task { await model.loadHistory() }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "bubble.left.and.text.bubble.right.fill")
                .font(.largeTitle)
                .foregroundStyle(.blue)
            Text("Ask anything about your data")
                .font(.headline)
            Text("\u{201C}Should I train hard today?\u{201D}\n\u{201C}How's my protein this week?\u{201D}\n\u{201C}Why is my recovery low?\u{201D}")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 80)
    }

    private func bubble(_ message: CoachMessage) -> some View {
        HStack {
            if message.role == "user" { Spacer(minLength: 48) }
            Text(message.content)
                .font(.subheadline)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(
                    message.role == "user" ? Color.blue : Color.gray.opacity(0.15),
                    in: RoundedRectangle(cornerRadius: 18)
                )
                .foregroundStyle(message.role == "user" ? .white : .primary)
            if message.role != "user" { Spacer(minLength: 48) }
        }
        .padding(.horizontal)
        .id(message.id)
    }

    private var inputBar: some View {
        HStack(spacing: 10) {
            TextField("Ask your coach…", text: $model.draft, axis: .vertical)
                .lineLimit(1...4)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(.gray.opacity(0.12), in: RoundedRectangle(cornerRadius: 20))
                .focused($inputFocused)
                .onSubmit { Task { await model.send() } }
            Button {
                Task { await model.send() }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 30))
            }
            .disabled(model.isSending || model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }
}

#Preview {
    CoachView()
}
