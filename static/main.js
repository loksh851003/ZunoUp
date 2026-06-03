function toggleComments(postId) {
    var box = document.getElementById("comments-" + postId);
    if (box.style.display === "none") {
        box.style.display = "block";
    } else {
        box.style.display = "none";
    }
}

function likePost(postId) {
    fetch("/post/" + postId + "/like", {
        method: "POST"
    })
    .then(response => response.json())
    .then(data => {
        document.getElementById("like-count-" + postId).innerText = data.count;
    })
    .catch(error => console.error("Error:", error));
}