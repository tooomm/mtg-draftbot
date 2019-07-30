import numpy as np
import torch


class DraftBotModel(torch.nn.Module):
    """A pytorch model for learning archetype weights from magic the gathering
    draft data.

    Parameters
    ----------
    n_cards: int
      The number of cards in the set. This must bear a specific relationship to
      the dimensions of the input data, see the description of the `forward`
      method below.

    n_archetypes: int
      The number of draft archetypes to learn. This is a hyperparameter of the
      learning algorithm. Less archetypes increases the bias of the model, more
      archetypes increases the variance.

    Description of the Algorithm
    ----------------------------
    This algorithm is based on the draft simulation ideas contained in
    `draftbot.Draft`, so reading the documentation of that class should be
    considered prerequisite to understanding this learning algorithm.

    In `draftbot.Draft` we simulated magic the gathering drafting by having
    each drafter track a set of internal preferences for a number of deck
    archetypes. These preferences evolve throughout the draft as the given
    drafter picks cards from each pack. The evolution of these preferences are
    driven by a set of hidden parameters: the weight of each card in the set
    for each deck archetype (i.e. cards which are more desirable in a given
    archetype have a higher weight). It is these weights that are learned here.

    The forward pass of learning proceeds by mimicking the process in
    `draftbot.Draft` for computing the preferences for each drafter at each
    given pick. To do so, the input data for each pass contains both the
    drafters current cards held, and the available cards for selection from the
    drafter's current pack.

    Attributes
    ----------
    weights: torch.nn.Parameter, shape (n_cards, n_archetypes)
      The archetype weights to be learned from draft data. Initialized as random
      noise, then learned through gradient descent.
    """
    def __init__(self, *, n_cards, n_archetypes):
        super().__init__()
        self.n_cards = n_cards
        self.n_archetypes = n_archetypes
        self.weights = torch.nn.Parameter(
            torch.FloatTensor(n_cards, n_archetypes).uniform_(0.0, 1.0))
    
    def forward(self, X):
        """Forward pass through the model using the current weights.

        Parameters
        ----------
        X: torch.Tensor, shape (batch_size, 2 * n_cards)
          A tensor containing recorded draft data. Each row of this tensor
          contains data relevant to one pick of a single draft (there is no
          requirement that a single batch contains only data for a single
          drafter, or a single round). Columns of this tensor record two types
          of data:
            - The first n_cards columns of contain the options for the current
              drafter from the current pack. These should be either 0.0 (the
              card is not available to be picked), or 1.0 (the card is
              available to be picked).
            - The remaining n_cards columns contain the cards currently held by
              the drafter. These may not be binary, a 2.0 records that the
              drafter currently holds two of the given card.

        Returns
        -------
        log_probs: torch.Tensor, shape (batch_size, n_cards)
          The log-softmax of the pick preferences for each card in the given
          pack.
        """
        options, cards = X[:, :self.n_cards], X[:, self.n_cards:]
        archetype_preferences = cards @ self.weights + 1
        option_weights = (
            options.view((X.shape[0], self.n_cards, 1))
            * self.weights.reshape((1, self.n_cards, self.n_archetypes)))
        current_option_preferences = torch.einsum(
            'pw,pcw->pc', archetype_preferences, option_weights)
        log_probs = stable_non_zero_log_softmax(current_option_preferences)
        return log_probs


class DraftBotModelTrainer:
    """Utility for training draftbot models and tracking their performance.

    Parameters
    ----------
    n_epochs: int
      The number of training epochs.

    learning_rate: float
      Learning rate for gradient descent.

    alpha: float
      L2 regularization hyperparameter.

    loss_function: (torch.Tensor, Torch.tensor) -> float
      Loss function to minimize during training. Default is the negative
      log-likelihood loss.

    report_freq: int
      How often to report model performance. Default is each epoch.

    Output Attributes
    -----------------
    epoch_training_loss: List[float]
      The average batch training losses for each epoch of training.

    epoch_testing_loss: List[float]
      The average batch testing losses for each epoch of training.
    """
    def __init__(self, *,
                 n_epochs=25,
                 learning_rate=0.005,
                 alpha=0.001,
                 loss_function=torch.nn.NLLLoss,
                 report_freq=1):
        self.n_epochs = n_epochs
        self.learning_rate = learning_rate
        self.alpha = alpha
        self.report_freq = report_freq
        self.loss_function = loss_function
        self.epoch_training_losses = []
        self.epoch_testing_losses = []

    def fit(self, model, train_batcher, *, test_batcher=None):
        """Fit a model to draft data using gradient descent.

        Parameters
        ----------
        model: DraftBotModel
          The model to train.

        train_batcher: torch.utils.data.DataLoader
          Generator for training batches.

        test_batcher: torch.utils.data.DataLoader
          Generator for testing batches.
        """
        for epoch in range(self.n_epochs):
            batch_losses = []
            for Xb, yb in train_batcher:
                log_probs = model(Xb)
                raw_loss = self.loss_function(log_probs, yb)
                regularized_loss = (
                    raw_loss + self.alpha * torch.sum(model.weights ** 2))
                regularized_loss.backward()
                with torch.no_grad():
                    model.weights -= self.learning_rate * model.weights.grad
                    model.weights.grad.zero_()
                    batch_losses.append(raw_loss.item())
            epoch_loss = np.mean(batch_losses)
            self.epoch_training_losses.append(epoch_loss)
            if test_batcher:
                with torch.no_grad():
                    test_loss = np.mean([
                        self.loss_function(model(Xb), yb).item()
                        for Xb, yb in test_batcher])
                    self.epoch_testing_losses.append(test_loss)
            if epoch % self.report_freq == 0:
                self.report_loss(epoch_loss, epoch)
                if test_batcher:
                    self.report_loss(epoch_loss, epoch, kind="Test")

    def report_loss(self, loss, epoch, kind="Train"):
        print(f"{kind}ing loss, epoch {epoch}: {loss}")


def stable_non_zero_log_softmax(x):
    b = x.max(dim=1).values.view(-1, 1)
    stabalized_x = (x - b * x.sign())
    log_sum_exps = torch.log(torch.sum(x.sign() * torch.exp(stabalized_x), dim=1))
    log_probs = x.sign() * (stabalized_x - log_sum_exps.view(-1, 1))
    return log_probs
