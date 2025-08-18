# proctor/knn_module.py

import numpy as np

def knn(train, test, k=5):
    """
    K-Nearest Neighbors classifier.

    Args:
        train: numpy array of shape (m, n+1) where the last column is the label.
        test: flattened face image of shape (n,).
        k: number of neighbors to consider (default=5).

    Returns:
        Predicted class label.
    """
    distances = []

    for data in train:
        # Split the data into features and label
        x_data = data[:-1]
        y_label = data[-1]

        # Compute Euclidean distance
        dist = np.linalg.norm(x_data - test)
        distances.append((dist, y_label))

    # Sort based on distance and pick top k
    distances = sorted(distances, key=lambda x: x[0])[:k]

    # Extract the labels of the top k
    labels = np.array([label for _, label in distances])

    # Count frequency of each label
    unique_labels, counts = np.unique(labels, return_counts=True)

    # Return the label with the highest count
    return unique_labels[np.argmax(counts)]
